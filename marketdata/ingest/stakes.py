"""Schedule 13D and 13G ingest (E16): structured stake filings, from their XML to stored holders.

1. The subject company's listing (`filings`, its file number starting `005-`, D-061; routed by
   the watcher as `stake_parse`, or read by the stakes backfill) names the primary document,
   EDGAR's rendering of the XML; the XML is the same file name without the `xsl…/` folder
   (PLAN.md §9.15). It is fetched raw first, or read from the raw store when stored before
   (D-070); the folder's index.json is the fallback.
2. normalize/schedule13.py parses it; its issuer must be the listing's company.
3. The filings about the company (its predecessors' too) are stored and decided together under a
   lock: each holder's latest filing about each class is served. A served stake that a later
   filing of its holder replaces, or that a new parser version reads otherwise, is logged in
   `value_changes` and announced as `data.changed` (D-027).

The backfill (`marketdata backfill stakes`, E16-S4) works per company: it reads the structured
filings already listed in `filings` about the company and its predecessors, and stores them in
one transaction, so a first backfill announces nothing: history isn't a change. It watches no
company (a watch would route the newest filings as live work), and a filing parsed before by the
same parser version is neither fetched nor parsed again unless forced.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import (
    cusips,
    filings,
    predecessors,
    securities,
    stakes,
    value_changes,
)
from marketdata.db.repositories.filings import Filing
from marketdata.ingest.announce import announce
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.changes import ValueChange
from marketdata.normalize.filing_events import SUBJECT_FILE_NUMBER
from marketdata.normalize.form4 import xml_names_in_index
from marketdata.normalize.schedule13 import (
    DOCUMENT_TYPES,
    METHOD,
    PARSER_VERSION,
    parse_schedule13,
    xml_document_name,
)
from marketdata.normalize.security_master import SecFileError
from marketdata.normalize.stakes import (
    StakeRecord,
    by_accession,
    filing_checks,
    holdings,
    share_classes,
    stake_changes,
)
from marketdata.normalize.tickers import primary_of
from marketdata.normalize.webhooks import DATA_CHANGED
from marketdata.sec import urls
from marketdata.sec.client import SecNotFound

log = structlog.get_logger(__name__)

STAKE_FORMS = tuple(sorted(DOCUMENT_TYPES))


class StakeRefused(Exception):
    """The filing can't be stored from this listing, whatever is retried: no such listing, the
    filer's own listing, a form that isn't structured, or a document about another company."""


@dataclass(frozen=True)
class StakeReport:
    accession_no: str
    cik: int
    form: str
    persons: int
    raw_key: str
    fetched: bool  # False: the XML was read from the raw store
    changes: int  # served stakes this run changed (value_changes rows)


@dataclass(frozen=True)
class StakesBackfillReport:
    cik: int
    group: tuple[int, ...]  # the company and its predecessors, whose listings were read
    listed: int  # structured filings listed about them
    parsed: tuple[str, ...]  # accession numbers parsed and stored
    kept: int  # parsed before by this parser version: not read again
    refused: tuple[str, ...]  # accession numbers that can't be stored (logged with the reason)
    changes: int


class StakeIngest:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def run(self, cik: int, accession_no: str) -> StakeReport:
        """Fetch, parse and store the stake filing `accession_no` from the listing of `cik`,
        the company it is about."""
        accession_no = urls.accession_no(accession_no)
        async with self._connect() as conn:
            listed = [
                listing
                for listing in await filings.listings(conn, [accession_no])
                if listing.cik == cik
            ]
        if not listed:
            raise StakeRefused(f"{accession_no} is not listed under CIK {cik}")
        record, fetched = await self.read(listed[0])
        async with self._connect() as conn, conn.transaction():
            changes = await self.store(conn, cik, [record])
        report = StakeReport(
            accession_no=accession_no,
            cik=cik,
            form=record.filing.form,
            persons=len(record.filing.persons),
            raw_key=record.raw_key,
            fetched=fetched,
            changes=len(changes),
        )
        log.info(
            "stake_stored",
            cik=cik,
            accession_no=accession_no,
            form=report.form,
            persons=report.persons,
            fetched=fetched,
            changes=report.changes,
        )
        return report

    async def read(self, listing: Filing) -> tuple[StakeRecord, bool]:
        """A listed filing's XML, parsed and checked against the listing; whether it was fetched
        now. StakeRefused or SecFileError when it can't be stored from this listing."""
        if not (listing.file_number or "").startswith(SUBJECT_FILE_NUMBER):
            raise StakeRefused(
                f"{listing.accession_no}: CIK {listing.cik}'s listing is the filer's own, not the "
                f"subject company's (file number {listing.file_number!r}, D-061)"
            )
        if listing.form not in DOCUMENT_TYPES:
            raise StakeRefused(
                f"{listing.accession_no}: {listing.form} is not a structured 13D or 13G"
            )
        doc_name, raw_key, data, fetched = await self._xml(
            listing.cik, listing.accession_no, listing.primary_doc
        )
        filing = parse_schedule13(data, listing.accession_no)
        if filing.issuer_cik != listing.cik:
            raise StakeRefused(
                f"{listing.accession_no}: the filing is about CIK {filing.issuer_cik}, not the "
                f"listing's company {listing.cik}"
            )
        if filing.form != listing.form:
            log.warning(
                "stake_form_differs",
                cik=listing.cik,
                accession_no=listing.accession_no,
                listed=listing.form,
                document=filing.form,
            )
        checks, flags = filing_checks(listing.form, filing.form, filing.persons)
        record = StakeRecord(
            accession_no=listing.accession_no,
            filed_at=listing.filed_at,
            doc_name=doc_name,
            raw_key=raw_key,
            filing=filing,
            checks=checks,
            flags=flags,
        )
        return record, fetched

    async def store(
        self, conn: AsyncConnection[Any], cik: int, records: Sequence[StakeRecord]
    ) -> list[ValueChange]:
        """Store filings about `cik`'s company in the caller's transaction, decide its holders
        again and log and announce the served stakes that changed."""
        head, group = await company_group(conn, cik)
        await stakes.lock(conn, group)
        before = await stakes.company_stakes(conn, group)
        for record in records:
            await stakes.store(conn, record, method=METHOD, version=PARSER_VERSION)
        after = await stakes.company_stakes(conn, group)
        classes, primary = await share_classes_of(
            conn, head, group, [cusip for filing in after for cusip in filing.cusips]
        )
        old, new = by_accession(before), by_accession(after)
        changes = stake_changes(
            holdings(before, classes, primary), holdings(after, classes, primary), old, new
        )
        for change in changes:
            listing_cik = old[change.entity_key["accession_no"]].listing_cik
            for change_id in await value_changes.record_stake_changes(conn, listing_cik, [change]):
                await announce(conn, DATA_CHANGED, str(change_id))
        return changes

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
                    "stake_xml_not_at_name",
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
                parse_schedule13(data, accession_no)
            except SecFileError:
                continue
            return name, raw_key, data, fetched
        raise StakeRefused(f"{accession_no}: the filing folder has no structured 13D or 13G XML")


class StakesBackfill:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._ingest = StakeIngest(fetcher, connect)
        self._connect = connect

    async def run(self, cik: int, *, force: bool = False) -> StakesBackfillReport:
        """Parse and store every structured 13D and 13G listed about `cik` and its predecessors,
        in one transaction. `force` reads filings parsed before by this version again."""
        async with self._connect() as conn:
            _, group = await company_group(conn, cik)
            listed = await filings.subject_listings(conn, group, STAKE_FORMS, SUBJECT_FILE_NUMBER)
            stored = {
                filing.accession_no: filing.extractor_version
                for filing in await stakes.filings(conn, [row.accession_no for row in listed])
            }
        records: list[StakeRecord] = []
        refused: list[str] = []
        kept = 0
        for listing in listed:
            if not force and stored.get(listing.accession_no) == PARSER_VERSION:
                kept += 1
                continue
            try:
                record, _ = await self._ingest.read(listing)
            except (StakeRefused, SecFileError, SecNotFound) as exc:
                log.warning(
                    "stake_refused",
                    cik=listing.cik,
                    accession_no=listing.accession_no,
                    error=str(exc),
                )
                refused.append(listing.accession_no)
                continue
            records.append(record)
        async with self._connect() as conn, conn.transaction():
            changes = await self._ingest.store(conn, cik, records)
        report = StakesBackfillReport(
            cik=cik,
            group=tuple(group),
            listed=len(listed),
            parsed=tuple(record.accession_no for record in records),
            kept=kept,
            refused=tuple(refused),
            changes=len(changes),
        )
        log.info(
            "stakes_backfill_done",
            cik=cik,
            group=list(group),
            listed=report.listed,
            parsed=len(report.parsed),
            kept=kept,
            refused=len(refused),
            changes=report.changes,
        )
        return report


async def company_group(conn: AsyncConnection[Any], cik: int) -> tuple[int, list[int]]:
    """The company a listing CIK belongs to now (itself, or the successor of a predecessor,
    D-068) and that company's group: itself first, then its predecessors."""
    head = (await predecessors.successors(conn, [cik])).get(cik, cik)
    return head, [head, *await predecessors.predecessors_of(conn, head)]


async def share_classes_of(
    conn: AsyncConnection[Any], head: int, group: Sequence[int], filed: Sequence[str]
) -> tuple[dict[str, str], str | None]:
    """The classes the CUSIP map links these filed CUSIPs to among the group's securities, and
    the company's primary class, which takes every other CUSIP (§9.15)."""
    links = await cusips.links(conn, sorted(set(filed))) if filed else []
    classes = share_classes(((link.cusip, link.ticker, link.cik) for link in links), set(group))
    listed = await securities.list_current_for_cik(conn, head)
    return classes, primary_of((row.ticker, row.security_type) for row in listed)
