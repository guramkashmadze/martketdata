"""E9-S5: the funds backfill loads a curated manager's filings, then queues one f13_parse task
per 13F report filed in the window, behind live work (D-067), and the CUSIP mapping after them;
the manager's first watch routes nothing (D-063)."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, connect_to, sec_serving

from marketdata.db.repositories import managers
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.f13 import F13Refused, FundsBackfill, FundsBackfillReport
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.cusips import CUSIP_MAP, PENDING
from marketdata.normalize.filings import F13_PARSE, FORM4_PARSE
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import build_registry

Conn = psycopg.AsyncConnection[Any]
MARKEL = 1096343
SUBMISSIONS = (SEC_FIXTURES / "submissions" / "MKL-manager-20261031.json").read_bytes()


async def _backfill(conn: Conn, tmp_path: Path, cik: int, since: date) -> FundsBackfillReport:
    async with sec_serving({urls.submissions(MARKEL): SUBMISSIONS}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        backfill = FundsBackfill(fetcher, connect_to(conn), build_registry().kinds)
        return await backfill.run(cik, since)


async def test_each_13f_report_in_the_window_is_queued_and_the_cusips_after_them(
    conn: Conn, tmp_path: Path
) -> None:
    manager = CuratedManager(MARKEL, "MARKEL GROUP INC.", "Markel Group", "test")
    await managers.replace_curated(conn, [MARKEL], [manager])

    report = await _backfill(conn, tmp_path, MARKEL, date(2026, 1, 1))

    # Markel's 13F-HRs for Q1, Q2 and Q3 2026, oldest first.
    assert report.queued == (
        "0001096343-26-000044",
        "0001096343-26-000065",
        "0001096343-26-000085",
    )
    parsed = await queue.list_tasks(conn, kind=F13_PARSE)
    assert {task.key for task in parsed} == set(report.queued)
    assert all(task.priority == queue.PRIORITY_BACKFILL for task in parsed)
    assert all(task.payload == {"cik": MARKEL, "accession_no": task.key} for task in parsed)
    [mapping] = await queue.list_tasks(conn, kind=CUSIP_MAP)
    assert (mapping.key, mapping.priority) == (PENDING, queue.PRIORITY_BACKFILL - 1)
    assert await queue.list_tasks(conn, kind=FORM4_PARSE) == []  # the first watch routes nothing


async def test_a_cik_that_isnt_a_curated_manager_is_refused(conn: Conn, tmp_path: Path) -> None:
    with pytest.raises(F13Refused, match="CIK 1096343 is not a curated manager"):
        await _backfill(conn, tmp_path, MARKEL, date(2026, 1, 1))
