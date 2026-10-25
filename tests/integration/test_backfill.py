"""E3-S5: the filing history backfill watches a company, then reads the older submissions
pages back to the start date; older pages route nothing.

Apple's page 001 was served on 2026-10-24 with every acceptance time shifted (D-057), so its
filings are stored without times.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from conftest import SEC_FIXTURES, LoadMaster, connect_to, sec_serving

from marketdata.db.repositories import filings
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import BackfillReport, FilingsWatcher
from marketdata.normalize.filings import EARNINGS_EXTRACT
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue

Conn = psycopg.AsyncConnection[Any]
AAPL = 320193
MAIN = (SEC_FIXTURES / "submissions" / "AAPL-20261025.json").read_bytes()
PAGE = (SEC_FIXTURES / "submissions" / "AAPL-page-001-20261024.json").read_bytes()
PAGE_URL = urls.submissions_page("CIK0000320193-submissions-001.json")
KINDS = frozenset({EARNINGS_EXTRACT})


def _page_rows_since(since: date) -> int:
    return sum(1 for filed in json.loads(PAGE)["filingDate"] if date.fromisoformat(filed) >= since)


async def _backfill(
    conn: Conn, tmp_path: Path, since: date, routes: dict[str, bytes | int]
) -> BackfillReport:
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await FilingsWatcher(fetcher, connect_to(conn), KINDS).backfill(AAPL, since)


async def test_the_backfill_reads_older_pages_back_to_the_start_date(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    since = date(2010, 1, 1)

    report = await _backfill(conn, tmp_path, since, {urls.submissions(AAPL): MAIN, PAGE_URL: PAGE})

    [page] = report.pages
    assert report.watch.stored is not None and report.watch.stored.inserted == 1002
    assert page.inserted == _page_rows_since(since) > 0
    assert not page.acceptance.trusted  # the whole page was checked, then cut to the window
    assert page.acceptance.covered > page.inserted
    assert await filings.count_for_company(conn, AAPL) == 1002 + page.inserted
    oldest = await filings.list_for_company(conn, AAPL, since=since, limit=5000)
    assert min(row.filed_at for row in oldest) >= since
    assert all(row.accepted_at is None for row in oldest if row.filed_at.year < 2015)
    assert await queue.list_tasks(conn, kind=EARNINGS_EXTRACT) == []  # pages never route


async def test_pages_older_than_the_start_date_are_not_read(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()

    # The page isn't served: reading it would fail the test.
    report = await _backfill(conn, tmp_path, date(2021, 10, 26), {urls.submissions(AAPL): MAIN})

    assert report.pages == ()
    assert report.inserted == 1002


async def test_a_second_backfill_adds_nothing(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    routes: dict[str, bytes | int] = {urls.submissions(AAPL): MAIN, PAGE_URL: PAGE}
    first = await _backfill(conn, tmp_path, date(2010, 1, 1), routes)

    again = await _backfill(conn, tmp_path, date(2010, 1, 1), routes)

    assert first.inserted > 1002
    assert again.watch.stored is None  # the main file had not changed
    assert again.inserted == 0
