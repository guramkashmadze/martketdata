"""Filings watcher: finds new filings in submissions JSON and enqueues their work (E3-S4, S5).

A watch of one company:

1. fetches its submissions JSON. Bytes equal to the file processed last time are not stored
   or parsed again; only the check time is recorded (D-059);
2. stores a changed file in the raw store, then parses `filings.recent`;
3. checks the file's acceptance times; a file that fails gives no times (D-057);
4. in one transaction, under a lock per company: inserts the listings not stored yet, fills
   acceptance times stored as missing, enqueues the tasks §4 routes new filings to (only for
   registered kinds) as live work, ahead of backfills (D-067) and marked `live`, announces new
   filings with a high-importance label to webhook destinations (`filing.event`, E5b), and
   records the file processed.

A company's first watch only records what is there: history goes through each epic's backfill,
so the first watch routes nothing.

The backfill (E3-S5) watches the company, then reads the older pages its file lists, as far back
as asked. Those filings are more than a year old and never routed. A page's acceptance times
are checked on the whole page before the rows outside the window are dropped.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from marketdata.db.repositories import companies, filings, tasks, watch_state
from marketdata.ingest.announce import announce
from marketdata.ingest.fetch import Connect, Fetched, RawFetcher
from marketdata.normalize.filings import (
    AcceptanceCheck,
    FilingRow,
    Submissions,
    check_acceptance_times,
    parse_submissions,
    parse_submissions_page,
    plan_filings,
    route,
    without_acceptance_times,
)
from marketdata.normalize.security_master import SecFileError
from marketdata.normalize.webhooks import FILING_EVENT, announces_filing
from marketdata.sec import urls

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StoreReport:
    """What one file added."""

    raw_key: str
    listed: int
    inserted: int
    filled: int
    differ: int
    acceptance: AcceptanceCheck
    routed: tuple[str, ...]  # "kind key" of each task enqueued


@dataclass(frozen=True)
class WatchReport:
    cik: int
    first_watch: bool
    stored: StoreReport | None  # None: the file had not changed


@dataclass(frozen=True)
class BackfillReport:
    cik: int
    since: date
    watch: WatchReport
    pages: tuple[StoreReport, ...]  # the older pages read, newest first

    @property
    def inserted(self) -> int:
        recent = self.watch.stored.inserted if self.watch.stored else 0
        return recent + sum(page.inserted for page in self.pages)


class FilingsWatcher:
    def __init__(self, fetcher: RawFetcher, connect: Connect, kinds: frozenset[str]) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._kinds = kinds  # registered task kinds: the only ones work is routed to (§4)

    async def watch(self, cik: int) -> WatchReport:
        """Look for new filings of `cik` and enqueue their work."""
        async with self._connect() as conn:
            state = await watch_state.get(conn, cik)
        fetched = await self._fetcher.fetch_if_changed(
            urls.submissions(cik), state.sha256 if state else None
        )
        if fetched.data is None:
            async with self._connect() as conn:
                await watch_state.record_check(conn, cik, fetched.fetched_at)
            log.debug("filings_unchanged", cik=cik)
            return WatchReport(cik, first_watch=False, stored=None)
        submissions = _own(parse_submissions(fetched.data), cik)
        report = await self._store(cik, submissions.filings, fetched, watched=True)
        return WatchReport(cik, first_watch=report.first_watch, stored=report.store)

    async def backfill(self, cik: int, since: date) -> BackfillReport:
        """Watch `cik`, then store its older filings filed on or after `since` from the pages
        its submissions file lists (PLAN.md §8)."""
        watch = await self.watch(cik)
        async with self._connect() as conn:
            state = await watch_state.get(conn, cik)
        if state is None:
            raise RuntimeError(f"CIK {cik} has no watch state after a watch")
        # The file the watch processed, now or earlier, lists the pages.
        submissions = _own(parse_submissions(await self._fetcher.read(state.raw_key)), cik)
        pages = []
        for page in submissions.pages:
            if page.filed_to < since:
                continue
            fetched = await self._fetcher.fetch_if_changed(urls.submissions_page(page.name), None)
            if fetched.data is None:
                raise RuntimeError(f"{page.name} was fetched but not stored")
            rows = parse_submissions_page(fetched.data, cik)
            check = check_acceptance_times(rows, fetched.fetched_at)
            window = [row for row in rows if row.filed_at >= since]
            stored = await self._store(cik, window, fetched, watched=False, check=check)
            pages.append(stored.store)
        report = BackfillReport(cik, since, watch, tuple(pages))
        log.info(
            "filings_backfilled",
            cik=cik,
            since=since.isoformat(),
            pages=len(pages),
            inserted=report.inserted,
        )
        return report

    async def _store(
        self,
        cik: int,
        rows: Sequence[FilingRow],
        fetched: Fetched,
        *,
        watched: bool,
        check: AcceptanceCheck | None = None,
    ) -> "_Stored":
        """Store the listings of one file; `watched` files route work and become the company's
        processed file. `check` is the file's acceptance check when `rows` are only part of it.
        """
        if fetched.key is None:
            raise ValueError("only a stored file can be stored")
        check = check or check_acceptance_times(rows, fetched.fetched_at)
        if not check.trusted:
            log.warning(
                "acceptance_times_dropped", cik=cik, raw_key=fetched.key, reason=check.reason
            )
            rows = without_acceptance_times(rows)
        routed: list[str] = []
        async with self._connect() as conn, conn.transaction():
            await watch_state.lock(conn, cik)
            state = await watch_state.get(conn, cik)
            stored = await filings.acceptance(conn, cik, [row.accession_no for row in rows])
            plan = plan_filings(stored, rows)
            inserted = set(await filings.insert_new(conn, plan.new))
            filled = await filings.fill_accepted_at(conn, cik, plan.fill)
            if watched and state is not None:
                company = await companies.get(conn, cik)
                filer_type = company.filer_type if company else "unknown"
                for row in plan.new:
                    if row.accession_no not in inserted:
                        continue
                    for work in route(row, filer_type=filer_type):
                        if work.kind in self._kinds:
                            await tasks.enqueue(
                                conn,
                                work.kind,
                                work.key,
                                {**work.payload, "live": True},
                                force=work.force,
                                priority=tasks.PRIORITY_LIVE,
                            )
                            routed.append(f"{work.kind} {work.key}")
                    if announces_filing(row.form, row.items_8k, row.file_number):
                        await announce(conn, FILING_EVENT, f"{row.accession_no}:{row.cik}")
            if watched:
                await watch_state.record_change(
                    conn, cik, fetched.sha256, fetched.key, fetched.fetched_at
                )
        for differ in plan.differ:
            log.warning(
                "accepted_at_differs",
                cik=cik,
                accession_no=differ.accession_no,
                stored=differ.stored.isoformat(),
                listed=differ.listed.isoformat(),
                raw_key=fetched.key,
            )
        report = StoreReport(
            raw_key=fetched.key,
            listed=len(rows),
            inserted=len(inserted),
            filled=filled,
            differ=len(plan.differ),
            acceptance=check,
            routed=tuple(routed),
        )
        log.info(
            "filings_stored",
            cik=cik,
            raw_key=fetched.key,
            listed=report.listed,
            inserted=report.inserted,
            filled=report.filled,
            routed=list(report.routed),
            first_watch=watched and state is None,
        )
        return _Stored(report, first_watch=watched and state is None)


@dataclass(frozen=True)
class _Stored:
    store: StoreReport
    first_watch: bool


def _own(submissions: Submissions, cik: int) -> Submissions:
    if submissions.cik != cik:
        raise SecFileError(f"submissions for CIK {cik} describe CIK {submissions.cik}")
    return submissions


def watch_summary(report: WatchReport) -> dict[str, Any]:
    """The fields a handler logs for a watch."""
    if report.stored is None:
        return {"cik": report.cik, "changed": False}
    return {
        "cik": report.cik,
        "changed": True,
        "inserted": report.stored.inserted,
        "routed": len(report.stored.routed),
        "first_watch": report.first_watch,
    }
