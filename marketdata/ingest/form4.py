"""Form 4 ingest (E8): one insider filing, from its XML to stored trades.

1. The issuer's listing (`filings`, routed by the watcher or queued by the insiders backfill)
   names the primary document, EDGAR's rendering of the XML; the XML is the same file name
   without the `xsl…/` folder (PLAN.md §8). It is fetched raw first, or read from the raw store
   when stored before (D-070). When that name isn't in the folder, the folder's index.json
   lists the XML.
2. normalize/form4.py parses it; its issuer must be the listing's company (D-063: the issuer's
   listing has no file number, an owner's has the issuer's).
3. The filing, its owners and its trades are stored in one transaction, with what that changes
   for the insider group (the filings the same owners made about the issuer): the lines a 4/A
   or a filing made twice supersedes are decided again for the whole group (E8-S3), so the
   result doesn't depend on which filing came first. Each served trade that changes, by an
   amendment, a duplicate's removal or a new parser version, is logged in `value_changes` and
   announced as `data.changed` (D-027).

The backfill (`marketdata backfill insiders`, E8-S4) works per company: it watches the company
so its listings are current and loads its predecessors' filings (D-068), then queues a
`form4_parse` task for each Form 4 or 4/A on their own listings filed in the window, behind
live work (D-067). Each filing's XML is parsed like a live one: one SEC request per filing,
none for an XML stored before (D-070).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import filings, insiders, predecessors, tasks, value_changes
from marketdata.db.repositories.insiders import StoredLine, supersession_of
from marketdata.ingest.announce import announce
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.changes import ValueChange
from marketdata.normalize.filings import FORM4_PARSE, INSIDER_FORMS
from marketdata.normalize.form4 import (
    METHOD,
    PARSER_VERSION,
    parse_form4,
    xml_document_name,
    xml_names_in_index,
)
from marketdata.normalize.insiders import (
    FilingRecord,
    GroupFiling,
    LineKey,
    group_supersessions,
    reparse_changes,
    supersession_changes,
)
from marketdata.normalize.security_master import SecFileError
from marketdata.normalize.webhooks import DATA_CHANGED
from marketdata.sec import urls
from marketdata.sec.client import SecNotFound

log = structlog.get_logger(__name__)


class Form4Refused(Exception):
    """The filing can't be stored from this listing, whatever is retried: no such listing, or
    a document whose issuer is another company."""


@dataclass(frozen=True)
class Form4Report:
    accession_no: str
    cik: int
    form: str
    owners: int
    lines: int
    raw_key: str
    fetched: bool  # False: the XML was read from the raw store
    changes: int  # served trades this run changed (value_changes rows)


class Form4Ingest:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def run(self, cik: int, accession_no: str) -> Form4Report:
        """Fetch, parse and store the Form 4 or 4/A `accession_no` from `cik`'s listing."""
        accession_no = urls.accession_no(accession_no)
        async with self._connect() as conn:
            listed = [
                listing
                for listing in await filings.listings(conn, [accession_no])
                if listing.cik == cik
            ]
        if not listed:
            raise Form4Refused(f"{accession_no} is not listed under CIK {cik}")
        listing = listed[0]
        doc_name, raw_key, data, fetched = await self._xml(cik, accession_no, listing.primary_doc)
        form4 = parse_form4(data, accession_no)
        if form4.issuer_cik != cik:
            raise Form4Refused(
                f"{accession_no}: the Form 4 is about CIK {form4.issuer_cik}, not the listing's "
                f"company {cik}"
            )
        if form4.document_type != listing.form:
            log.warning(
                "form4_type_differs",
                cik=cik,
                accession_no=accession_no,
                listed=listing.form,
                document=form4.document_type,
            )
        record = FilingRecord(accession_no, listing.filed_at, doc_name, raw_key, form4)
        async with self._connect() as conn, conn.transaction():
            changes = await self._store(conn, record)
        report = Form4Report(
            accession_no=accession_no,
            cik=cik,
            form=form4.document_type,
            owners=len(form4.owners),
            lines=len(form4.lines),
            raw_key=raw_key,
            fetched=fetched,
            changes=len(changes),
        )
        log.info(
            "form4_stored",
            cik=cik,
            accession_no=accession_no,
            form=report.form,
            owners=report.owners,
            lines=report.lines,
            fetched=fetched,
            changes=report.changes,
        )
        return report

    async def _store(self, conn: AsyncConnection[Any], record: FilingRecord) -> list[ValueChange]:
        """Store the filing, decide its insider group's supersessions again, and log and
        announce the served trades that changed."""
        previous = await insiders.get_filing(conn, record.accession_no)
        stored = await insiders.lines(conn, [record.accession_no])
        await insiders.store(conn, record, method=METHOD, version=PARSER_VERSION)
        changes: list[ValueChange] = []
        if previous is not None:
            changes += reparse_changes(
                record.accession_no,
                {line.line_no: line for line in stored if line.superseded_by is None},
                record.form4.lines,
                old_version=previous.extractor_version,
                new_version=PARSER_VERSION,
            )
        known = {(line.accession_no, line.line_no) for line in stored}
        changes += await self._supersede(conn, record, known)
        issuer = record.form4.issuer_cik
        for change_id in await value_changes.record_insider_changes(conn, issuer, changes):
            await announce(conn, DATA_CHANGED, str(change_id))
        return changes

    async def _supersede(
        self, conn: AsyncConnection[Any], record: FilingRecord, known: set[LineKey]
    ) -> list[ValueChange]:
        """Decide which lines of the filing's insider group are superseded; the changes to
        served trades. `known`: the filing's lines stored before this run."""
        group = await insiders.group_filings(conn, record.form4.issuer_cik, record.owner_ciks)
        stored = await insiders.lines(conn, [filing.accession_no for filing in group])
        lines: dict[LineKey, StoredLine] = {
            (line.accession_no, line.line_no): line for line in stored
        }
        decision = group_supersessions(
            [
                GroupFiling(
                    filing.accession_no,
                    filing.form,
                    filing.filed_at,
                    filing.original_filed_at,
                    [line for line in stored if line.accession_no == filing.accession_no],
                )
                for filing in group
            ]
        )
        now = {key: supersession_of(line) for key, line in lines.items()}
        updates = [
            (key, decision.superseded.get(key))
            for key in lines
            if now[key] != decision.superseded.get(key)
        ]
        await insiders.set_superseded(conn, updates)
        if record.accession_no in decision.unmatched:
            log.warning(
                "amendment_unmatched",
                cik=record.form4.issuer_cik,
                accession_no=record.accession_no,
                original_filed_at=str(record.form4.original_filed_at),
                owner_ciks=list(record.owner_ciks),
            )
        before = {
            key: state
            for key, state in now.items()
            if key[0] != record.accession_no or key in known
        }
        return supersession_changes(lines, before, decision.superseded)

    async def _xml(
        self, cik: int, accession_no: str, primary_doc: str | None
    ) -> tuple[str, str, bytes, bool]:
        """The filing's XML: its name, raw key, bytes and whether it was fetched now."""
        if primary_doc:
            name = xml_document_name(primary_doc)
            try:
                url = urls.filing_document(cik, accession_no, name)
                raw_key, data, fetched = await self._fetcher.fetch_document(url)
                return name, raw_key, data, fetched
            except (SecNotFound, ValueError) as exc:
                log.warning(
                    "form4_xml_not_at_name",
                    cik=cik,
                    accession_no=accession_no,
                    name=name,
                    error=str(exc),
                )
        return await self._xml_from_index(cik, accession_no)

    async def _xml_from_index(self, cik: int, accession_no: str) -> tuple[str, str, bytes, bool]:
        _, index, _ = await self._fetcher.fetch_document(urls.filing_index_json(cik, accession_no))
        for name in xml_names_in_index(index, accession_no):
            raw_key, data, fetched = await self._fetcher.fetch_document(
                urls.filing_document(cik, accession_no, name)
            )
            try:
                parse_form4(data, accession_no)
            except SecFileError:
                continue
            return name, raw_key, data, fetched
        raise Form4Refused(f"{accession_no}: the filing folder has no Form 4 XML")


@dataclass(frozen=True)
class InsidersBackfillReport:
    cik: int
    since: date
    predecessors: tuple[int, ...]  # whose filings were loaded too
    queued: tuple[str, ...]  # accession numbers whose form4_parse task is queued


class InsidersBackfill:
    def __init__(self, fetcher: RawFetcher, connect: Connect, kinds: frozenset[str]) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._kinds = kinds  # registered task kinds (§4)

    async def run(self, cik: int, since: date, *, force: bool = False) -> InsidersBackfillReport:
        """Queue the Forms 4 and 4/A about `cik` and its predecessors filed on or after
        `since`. `force` parses filings parsed before again (a new parser version)."""
        if FORM4_PARSE not in self._kinds:
            raise RuntimeError(f"{FORM4_PARSE} is not a registered task kind")
        async with self._connect() as conn:
            earlier = await predecessors.predecessors_of(conn, cik)
        watcher = FilingsWatcher(self._fetcher, self._connect, self._kinds)
        await watcher.watch(cik)
        for predecessor in earlier:
            await watcher.backfill(predecessor, since)
        queued: list[str] = []
        async with self._connect() as conn, conn.transaction():
            listed = await filings.issuer_listings(
                conn, [cik, *earlier], sorted(INSIDER_FORMS), since
            )
            for filing in listed:
                await tasks.enqueue(
                    conn,
                    FORM4_PARSE,
                    filing.accession_no,
                    {"cik": filing.cik, "accession_no": filing.accession_no},
                    force=force,
                    priority=tasks.PRIORITY_BACKFILL,
                )
                queued.append(filing.accession_no)
        log.info(
            "insiders_backfill_queued",
            cik=cik,
            since=since.isoformat(),
            predecessors=earlier,
            queued=len(queued),
        )
        return InsidersBackfillReport(cik, since, tuple(earlier), tuple(queued))
