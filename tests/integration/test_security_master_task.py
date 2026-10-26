"""E3-S3: the daily `security_master` task wraps E2's load (PLAN.md §9.2).

A load the safety limit refuses, or a broken universe file, makes the task dead at once
(TaskRefused) and changes nothing; a company whose submissions failed makes it retry.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import (
    TEST_UNIVERSE,
    TEST_UNIVERSE_CSV,
    LoadMaster,
    connect_to,
    fixture_routes,
    sec_serving,
)

from marketdata.config import Settings
from marketdata.db.repositories import companies
from marketdata.ingest.fetch import RawFetcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import SecurityMasterIncomplete, build_registry, load_security_master
from marketdata.tasks.kinds import SECURITY_MASTER
from marketdata.tasks.registry import TaskContext, TaskRefused

Conn = psycopg.AsyncConnection[Any]
TODAY = datetime.now(UTC).date()


async def _run(
    conn: Conn,
    tmp_path: Path,
    routes: Mapping[str, bytes | int],
    universe: str = TEST_UNIVERSE_CSV,
    predecessors: str = "cik,predecessor_cik,source_url,note\n",
) -> None:
    (tmp_path / "universe.csv").write_text(universe)
    (tmp_path / "overrides.csv").write_text("ticker,security_type,source_url\n")
    (tmp_path / "predecessors.csv").write_text(predecessors)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql://unused@localhost/unused_test",
        universe_file=tmp_path / "universe.csv",
        security_overrides_file=tmp_path / "overrides.csv",
        predecessors_file=tmp_path / "predecessors.csv",
    )
    task = await queue.enqueue(conn, SECURITY_MASTER, TODAY.isoformat())
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        context = TaskContext(settings, connect_to(conn), fetcher, build_registry().kinds)
        await load_security_master(context, task)


def test_the_worker_runs_the_daily_load() -> None:
    assert SECURITY_MASTER in build_registry()


async def test_the_task_loads_the_security_master(conn: Conn, tmp_path: Path) -> None:
    await _run(conn, tmp_path, fixture_routes())

    universe = await companies.list_in_universe(conn)
    assert sorted(company.cik for company in universe) == sorted(set(TEST_UNIVERSE.values()))


async def test_lists_that_look_cut_short_are_refused_at_once(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master(as_of=TODAY)
    cut = fixture_routes()
    cut[urls.COMPANY_TICKERS_MF] = (
        b'{"fields":["cik","seriesId","classId","symbol"],"data":[[36405,"S1","C1","VOO"]]}'
    )

    with pytest.raises(TaskRefused, match="the fund list would end 444 of 445"):
        await _run(conn, tmp_path, cut)


async def test_a_broken_universe_file_is_refused_at_once(conn: Conn, tmp_path: Path) -> None:
    with pytest.raises(TaskRefused, match="universe.csv"):
        await _run(conn, tmp_path, fixture_routes(), universe="ticker\nAAPL\n")


async def test_a_company_whose_submissions_failed_makes_the_task_retry(
    conn: Conn, tmp_path: Path
) -> None:
    routes = fixture_routes()
    routes[urls.submissions(TEST_UNIVERSE["TSM"])] = 404

    with pytest.raises(
        SecurityMasterIncomplete, match=f"CIK {TEST_UNIVERSE['TSM']}: SEC returned 404"
    ):
        await _run(conn, tmp_path, routes)

    loaded = await companies.list_in_universe(conn)  # the rest of the load stands
    assert len(loaded) == len(set(TEST_UNIVERSE.values()))
