"""E7a-S1, S5: the earnings backfill queues one earnings_extract task per 8-K or 8-K/A with item
2.02 a company filed in the window, behind live work (D-067, D-069). A company's history under
a predecessor CIK is loaded first and included (D-068): XOM's releases before 2026-07-01 are
under Exxon Mobil Corp (34088).
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, connect_to, sec_serving

from marketdata.db.repositories import filings, predecessors
from marketdata.db.repositories.predecessors import CompanyPredecessor
from marketdata.ingest.earnings import EarningsBackfill, EarningsBackfillReport
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.filings import EARNINGS_EXTRACT, FilingRow, parse_submissions
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import build_registry

Conn = psycopg.AsyncConnection[Any]

AAPL, XOM, EXXON = 320193, 2115436, 34088
SINCE = date(2025, 10, 25)
XOM_8K12B = (
    "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/"
    "0001193125-26-291990-index.htm"
)


async def _company(conn: Conn, cik: int, *, in_universe: bool = True) -> None:
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (%s, 'test', 'domestic', %s)",
        [cik, in_universe],
    )


def _filing(accession: str, filed: date, form: str = "8-K", items: str = "2.02,9.01") -> FilingRow:
    return FilingRow(
        accession_no=accession,
        cik=AAPL,
        form=form,
        filed_at=filed,
        accepted_at=None,
        period_of_report=filed,
        items_8k=tuple(items.split(",")) if form.startswith("8-K") else None,
        primary_doc=None,
        is_xbrl=None,
        file_number=None,
    )


async def _backfill(
    conn: Conn,
    tmp_path: Path,
    cik: int,
    routes: dict[str, bytes | int] | None = None,
    *,
    force: bool = False,
) -> EarningsBackfillReport:
    async with sec_serving(routes or {}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        backfill = EarningsBackfill(fetcher, connect_to(conn), build_registry().kinds)
        return await backfill.run(cik, SINCE, force=force)


async def test_releases_filed_in_the_window_are_queued_behind_live_work(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)
    await filings.insert_new(
        conn,
        [
            _filing("0000320193-25-000071", date(2025, 7, 31)),  # before the window
            _filing("0000320193-25-000077", date(2025, 10, 30)),
            _filing("0000320193-26-000005", date(2026, 1, 29)),
            _filing("0000320193-26-000007", date(2026, 2, 3), form="8-K/A"),
            _filing("0000320193-26-000010", date(2026, 2, 24), items="5.07"),  # a vote
            _filing("0000320193-26-000012", date(2026, 5, 1), form="10-Q"),
        ],
    )

    report = await _backfill(conn, tmp_path, AAPL)

    assert report.queued == (
        "0000320193-25-000077",
        "0000320193-26-000005",
        "0000320193-26-000007",
    )
    tasks = await queue.list_tasks(conn, kind=EARNINGS_EXTRACT)
    assert sorted((task.key, task.priority, task.payload["cik"]) for task in tasks) == [
        (accession, queue.PRIORITY_BACKFILL, AAPL) for accession in report.queued
    ]


async def test_releases_done_before_run_again_only_with_force(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL)
    await filings.insert_new(conn, [_filing("0000320193-25-000077", date(2025, 10, 30))])
    await _backfill(conn, tmp_path, AAPL)
    claimed = await queue.claim(conn, [EARNINGS_EXTRACT], timedelta(minutes=5))
    assert claimed is not None
    await queue.complete(conn, claimed)

    await _backfill(conn, tmp_path, AAPL)
    [kept] = await queue.list_tasks(conn, kind=EARNINGS_EXTRACT)
    await _backfill(conn, tmp_path, AAPL, force=True)
    [again] = await queue.list_tasks(conn, kind=EARNINGS_EXTRACT)

    assert (kept.status, again.status) == ("done", "queued")


async def test_a_predecessors_filings_are_loaded_and_its_releases_queued(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, XOM)
    await _company(conn, EXXON, in_universe=False)
    await predecessors.replace_all(
        conn,
        [CompanyPredecessor(cik=XOM, predecessor_cik=EXXON, source_url=XOM_8K12B, note="test")],
    )
    own = parse_submissions((SEC_FIXTURES / "submissions" / "XOM.json").read_bytes())
    await filings.insert_new(conn, list(own.filings))
    routes: dict[str, bytes | int] = {
        urls.submissions(EXXON): (
            SEC_FIXTURES / "submissions" / "XOM-predecessor-34088.json"
        ).read_bytes()
    }

    report = await _backfill(conn, tmp_path, XOM, routes)

    assert report.predecessors == (EXXON,)
    assert await filings.count_for_company(conn, EXXON) > 0
    assert report.queued == (
        "0000034088-25-000059",  # Q3 2025, 2025-10-31
        "0000034088-26-000033",  # Q4 2025, 2026-01-30
        "0000034088-26-000065",  # Q1 2026, 2026-05-01
        "0002115436-26-000006",  # Q2 2026, 2026-07-31, the first under the new CIK
    )
    tasks = {task.key: task for task in await queue.list_tasks(conn, kind=EARNINGS_EXTRACT)}
    assert tasks["0000034088-26-000065"].payload == {
        "cik": EXXON,
        "accession_no": "0000034088-26-000065",
    }
    assert tasks["0002115436-26-000006"].payload["cik"] == XOM


async def test_the_backfill_needs_the_extraction_kind(conn: Conn, tmp_path: Path) -> None:
    async with sec_serving({}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        backfill = EarningsBackfill(fetcher, connect_to(conn), frozenset())

        with pytest.raises(RuntimeError, match="earnings_extract is not a registered"):
            await backfill.run(AAPL, SINCE)
