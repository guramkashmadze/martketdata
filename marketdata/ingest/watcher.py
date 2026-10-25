"""Filings watcher: finds new filings in submissions JSON and enqueues their work (E3-S4, S5).

A watch of one company:

1. fetches its submissions JSON. Bytes equal to the file processed last time are not stored
   or parsed again; only the check time is recorded (D-059);
2. stores a changed file in the raw store, then parses `filings.recent`;
3. checks the file's acceptance times; a file that fails gives no times (D-057);
4. in one transaction, under a lock per company: inserts the listings not stored yet, fills
   acceptance times stored as missing, enqueues the tasks §4 routes new filings to (only for
   registered kinds), and records the file processed.

A company's first watch only records what is there: history goes through each epic's backfill,
so the first watch routes nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from marketdata.db.repositories import companies, filings, tasks, watch_state
from marketdata.ingest.fetch import Connect, Fetched, RawFetcher
from marketdata.normalize.filings import (
    AcceptanceCheck,
    FilingRow,
    Submissions,
    check_acceptance_times,
    parse_submissions,
    plan_filings,
    route,
    without_acceptance_times,
)
from marketdata.normalize.security_master import SecFileError
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

    async def _store(
        self, cik: int, rows: Sequence[FilingRow], fetched: Fetched, *, watched: bool
    ) -> "_Stored":
        assert fetched.key is not None
        check = check_acceptance_times(rows, fetched.fetched_at)
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
                            await tasks.enqueue(conn, work.kind, work.key, work.payload)
                            routed.append(f"{work.kind} {work.key}")
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
