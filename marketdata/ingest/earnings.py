"""Earnings release ingest (E7a): from an 8-K with item 2.02 to verified facts.

Finding the press release (E7a-S1):

1. The filing's index page is fetched, raw first. Its "Accepted" time (Eastern) is the
   release's acceptance time; a different time in the submissions listing is logged (D-057).
2. The EX-99 exhibits are fetched in the order §9.9 tries them (EX-99.1, EX-99, EX-99.2 …),
   and each one's text, tables as markdown rows, is stored in `documents`.

Filing documents never change once filed, so a document stored before is read from the raw
store: re-running a release (new rules, a new override) makes no SEC request (D-070).

The backfill (`marketdata backfill earnings`, E7a-S5) works per company: it loads the filings
of the company's predecessors first (D-068), then queues an `earnings_extract` task for each
8-K or 8-K/A with item 2.02 filed in the window, behind live work (D-067, D-069).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import structlog

from marketdata.db.repositories import documents, filings, predecessors, tasks
from marketdata.db.repositories.documents import Document
from marketdata.extract.html_tables import parse_document
from marketdata.extract.interface import ReleaseDocument
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.filing_index import FilingIndex, earnings_exhibits, parse_filing_index
from marketdata.normalize.filings import EARNINGS_EXTRACT, EARNINGS_FORMS
from marketdata.normalize.security_master import SecFileError
from marketdata.sec import urls

log = structlog.get_logger(__name__)


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


class EarningsIngest:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

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
            document = ReleaseDocument(exhibit.name, exhibit.doc_type, parsed.text, parsed.tables)
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
