"""Earnings release ingest (E7a): from an 8-K with item 2.02 to verified facts.

Finding the press release (E7a-S1):

1. The filing's index page is fetched, raw first. Its "Accepted" time (Eastern) is the
   release's acceptance time; a different time in the submissions listing is logged (D-057).
2. The EX-99 exhibits are fetched in the order §9.9 tries them (EX-99.1, EX-99, EX-99.2 …),
   and each one's text, tables as markdown rows, is stored in `documents`.

Extracting, verifying and storing (E7a-S3, S4):

3. The rule extractor reads the exhibits, with the company's overrides; a bank has no revenue
   slot (D-072), nor any slot an override marks not applicable.
4. normalize/earnings.py checks every value against the stored text (verify/) and builds the
   rows; they are stored in one transaction with a `value_changes` row for each served value a
   re-run (correction) or an 8-K/A (amendment) changes (D-027, D-077), each announced to
   webhook destinations as `data.changed` (E5b). A release the watcher routed when it was
   filed (`live`) that is complete or partial is announced as `earnings.verified`, once.
5. The company's releases are cross-checked against the 10-Qs and 10-Ks already loaded
   (ingest/cross_check.py, E4b-S6).

Filing documents never change once filed, so a document stored before is read from the raw
store: re-running a release (new rules, a new override) makes no SEC request (D-070).

The backfill (`marketdata backfill earnings`, E7a-S5) works per company: it loads the filings
of the company's predecessors first (D-068), then queues an `earnings_extract` task for each
8-K or 8-K/A with item 2.02 filed in the window, behind live work (D-067, D-069).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import structlog

from marketdata.db.repositories import (
    companies,
    documents,
    earnings,
    filings,
    predecessors,
    tasks,
    value_changes,
)
from marketdata.db.repositories.documents import Document
from marketdata.extract.earnings_rules import (
    Override,
    OverrideError,
    RuleExtractor,
    parse_overrides,
)
from marketdata.extract.html_tables import parse_document
from marketdata.extract.interface import Release, ReleaseDocument
from marketdata.ingest.announce import announce
from marketdata.ingest.cross_check import cross_check_company
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.changes import (
    Served,
    ValueChange,
    amendment_changes,
    correction_changes,
)
from marketdata.normalize.earnings import ReleaseRecord, assemble, not_applicable_slots
from marketdata.normalize.filing_index import FilingIndex, earnings_exhibits, parse_filing_index
from marketdata.normalize.filings import EARNINGS_EXTRACT, EARNINGS_FORMS
from marketdata.normalize.security_master import SecFileError
from marketdata.normalize.webhooks import DATA_CHANGED, EARNINGS_VERIFIED
from marketdata.sec import urls

log = structlog.get_logger(__name__)

VERIFIED = ("complete", "partial")  # a release that serves verified values


def read_overrides(path: Path) -> tuple[Override, ...]:
    """The extraction overrides file (EXTRACTION_OVERRIDES_FILE), checked: each source_url must
    be an SEC filing page. OverrideError lists every problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise OverrideError(f"{path}: file not found") from None
    overrides = parse_overrides(text, str(path))
    problems = []
    for number, override in enumerate(overrides, start=1):
        parsed = urls.parse(override.source_url)
        if parsed is None or parsed.endpoint != "filing":
            problems.append(
                f"override {number} (CIK {override.cik}, {override.slot}): source_url is not "
                f"an SEC filing page: {override.source_url}"
            )
    if problems:
        raise OverrideError(f"{path}:\n" + "\n".join(f"  - {line}" for line in problems))
    return overrides


@dataclass(frozen=True)
class StoredExhibit:
    document: ReleaseDocument
    raw_key: str


@dataclass(frozen=True)
class FetchedRelease:
    """A release's index page and exhibits, stored."""

    cik: int  # the listing company whose folder the files are in
    accession_no: str
    index: FilingIndex
    listed_accepted_at: datetime | None  # the submissions listing's time; None if not stored
    exhibits: tuple[StoredExhibit, ...]  # in the order they are tried
    fetched: int  # files fetched from the SEC by this run; 0 when all were stored before
    skipped: tuple[str, ...] = ()  # exhibits that couldn't be fetched, with why

    @property
    def documents(self) -> tuple[ReleaseDocument, ...]:
        return tuple(exhibit.document for exhibit in self.exhibits)


@dataclass(frozen=True)
class ExtractedRelease:
    fetched: FetchedRelease
    record: ReleaseRecord
    changes: tuple[ValueChange, ...]  # served values this run changed


class EarningsIngest:
    def __init__(
        self, fetcher: RawFetcher, connect: Connect, overrides: Sequence[Override] = ()
    ) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._overrides = tuple(overrides)

    async def run(self, cik: int, accession_no: str, *, live: bool = False) -> ExtractedRelease:
        """Fetch, extract, verify and store one release (steps 1-4 above). `live`: the watcher
        routed it when it was filed."""
        fetched = await self.fetch(cik, accession_no)
        async with self._connect() as conn:
            company = await companies.get(conn, cik)
            group = [cik, *await predecessors.predecessors_of(conn, cik)]
        fiscal_year_end = company.fiscal_year_end if company else None
        not_applicable, overridden = not_applicable_slots(
            cik, fetched.accession_no, company.sic_code if company else None, self._overrides
        )
        release = Release(
            cik,
            fetched.accession_no,
            fetched.index.form,
            fetched.documents,
            not_applicable,
            fetched.index.filed_at,
            fiscal_year_end,
        )
        result = RuleExtractor(self._overrides).extract(release)
        record = assemble(
            result,
            {document.doc_name: document.text for document in fetched.documents},
            accession_no=fetched.accession_no,
            cik=cik,
            accepted_at=fetched.index.accepted_at,
            fiscal_year_end=fiscal_year_end,
            report={
                "form": fetched.index.form,
                "exhibits": [
                    {"doc_name": exhibit.document.doc_name, "doc_type": exhibit.document.doc_type}
                    for exhibit in fetched.exhibits
                ],
                "skipped_exhibits": list(fetched.skipped),
                "accepted_at": {
                    "index_page": fetched.index.accepted_at.isoformat(),
                    "listed": _iso(fetched.listed_accepted_at),
                },
                "not_applicable_overrides": overridden,
            },
        )
        changes = await self._store(record, group, fetched.index.form, live=live)
        # Its 10-Q or 10-K may be loaded already (a re-run, an 8-K/A): check it now (E4b-S6).
        await cross_check_company(self._connect, cik)
        log.info(
            "earnings_release_stored",
            cik=cik,
            accession_no=fetched.accession_no,
            status=record.status,
            period_end=_iso(record.period_end),
            slots={fact.slot: fact.check_status for fact in record.facts},
            changes=len(changes),
        )
        return ExtractedRelease(fetched, record, tuple(changes))

    async def _store(
        self, record: ReleaseRecord, group: Sequence[int], form: str, *, live: bool = False
    ) -> list[ValueChange]:
        new = [
            Served(fact.slot, fact.value, fact.prior_value, fact.extractor_version)
            for fact in record.facts
        ]
        async with self._connect() as conn, conn.transaction():
            stored = await earnings.served(conn, record.accession_no)
            changes: list[ValueChange] = []
            if stored:
                changes = correction_changes(record.accession_no, stored, new)
            elif form.endswith("/A") and record.period_end is not None:
                amended = await earnings.amended_release(
                    conn, group, record.period_end, record.accession_no
                )
                if amended is not None:
                    original = await earnings.served(conn, amended)
                    changes = amendment_changes(record.accession_no, amended, original, new)
            await earnings.store(conn, record, live=live)
            for change_id in await value_changes.record_fact_changes(conn, record.cik, changes):
                await announce(conn, DATA_CHANGED, str(change_id))
            release = await earnings.get_release(conn, record.accession_no)
            if release is not None and release.live and release.status in VERIFIED:
                await announce(conn, EARNINGS_VERIFIED, record.accession_no)
        return changes

    async def fetch(self, cik: int, accession_no: str) -> FetchedRelease:
        """Store the index page and EX-99 exhibits of `accession_no` from `cik`'s folder."""
        accession_no = urls.accession_no(accession_no)
        _, page, fresh = await self._fetcher.fetch_document(
            urls.filing_index_page(cik, accession_no)
        )
        fetched = int(fresh)
        index = parse_filing_index(page)
        if index.accession_no != accession_no:
            raise SecFileError(f"the index page of {accession_no} is for {index.accession_no}")
        listed = await self._listed_accepted_at(cik, accession_no, index)
        exhibits: list[StoredExhibit] = []
        skipped: list[str] = []
        for exhibit in earnings_exhibits(index):
            try:
                url = urls.filing_document(cik, accession_no, exhibit.name)
            except ValueError as exc:
                skipped.append(f"{exhibit.name}: {exc}")
                continue
            raw_key, data, fresh = await self._fetcher.fetch_document(url)
            fetched += int(fresh)
            parsed = parse_document(exhibit.name, data)
            document = ReleaseDocument(
                exhibit.name, exhibit.doc_type, parsed.text, parsed.tables, parsed.images
            )
            exhibits.append(StoredExhibit(document, raw_key))
            if not parsed.tables and parsed.images:
                # Pages printed as pictures, with their text in a hidden layer (EDGE_CASES.md).
                log.warning(
                    "exhibit_is_page_images",
                    cik=cik,
                    accession_no=accession_no,
                    doc_name=exhibit.name,
                    images=parsed.images,
                )
        await self._store_documents(accession_no, exhibits)
        for reason in skipped:
            log.warning("exhibit_skipped", cik=cik, accession_no=accession_no, reason=reason)
        if not exhibits:
            log.warning(
                "earnings_exhibit_not_found",
                cik=cik,
                accession_no=accession_no,
                documents=[document.doc_type for document in index.documents],
            )
        return FetchedRelease(
            cik, accession_no, index, listed, tuple(exhibits), fetched, tuple(skipped)
        )

    async def _listed_accepted_at(
        self, cik: int, accession_no: str, index: FilingIndex
    ) -> datetime | None:
        async with self._connect() as conn:
            listed = (await filings.acceptance(conn, cik, [accession_no])).get(accession_no)
        if listed is not None and listed != index.accepted_at:
            log.warning(
                "release_accepted_at_differs",
                cik=cik,
                accession_no=accession_no,
                index_page=index.accepted_at.isoformat(),
                listed=listed.isoformat(),
            )
        return listed

    async def _store_documents(self, accession_no: str, exhibits: Sequence[StoredExhibit]) -> None:
        rows = [
            Document(
                accession_no=accession_no,
                doc_name=exhibit.document.doc_name,
                doc_type=exhibit.document.doc_type,
                raw_key=exhibit.raw_key,
                text=exhibit.document.text,
            )
            for exhibit in exhibits
        ]
        async with self._connect() as conn:
            await documents.upsert(conn, rows)


def _iso(moment: date | datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


EARNINGS_ITEM = "2.02"


@dataclass(frozen=True)
class EarningsBackfillReport:
    cik: int
    since: date
    predecessors: tuple[int, ...]  # whose filings were loaded first
    queued: tuple[str, ...]  # accession numbers whose earnings_extract task is queued


class EarningsBackfill:
    def __init__(self, fetcher: RawFetcher, connect: Connect, kinds: frozenset[str]) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._kinds = kinds  # registered task kinds (§4)

    async def run(self, cik: int, since: date, *, force: bool = False) -> EarningsBackfillReport:
        """Queue the extraction of `cik`'s earnings releases filed on or after `since`, and its
        predecessors'. `force` runs releases already extracted again (new rules, overrides)."""
        if EARNINGS_EXTRACT not in self._kinds:
            raise RuntimeError(f"{EARNINGS_EXTRACT} is not a registered task kind")
        async with self._connect() as conn:
            earlier = await predecessors.predecessors_of(conn, cik)
        watcher = FilingsWatcher(self._fetcher, self._connect, self._kinds)
        for predecessor in earlier:
            await watcher.backfill(predecessor, since)
        queued = []
        async with self._connect() as conn, conn.transaction():
            listed = await filings.with_item(
                conn, [cik, *earlier], sorted(EARNINGS_FORMS), EARNINGS_ITEM, since
            )
            for filing in listed:
                if filing.accession_no in queued:
                    continue  # listed under the company and its predecessor
                await tasks.enqueue(
                    conn,
                    EARNINGS_EXTRACT,
                    filing.accession_no,
                    {"cik": filing.cik, "accession_no": filing.accession_no},
                    force=force,
                    priority=tasks.PRIORITY_BACKFILL,
                )
                queued.append(filing.accession_no)
        log.info(
            "earnings_backfill_queued",
            cik=cik,
            since=since.isoformat(),
            predecessors=earlier,
            queued=len(queued),
        )
        return EarningsBackfillReport(cik, since, tuple(earlier), tuple(queued))
