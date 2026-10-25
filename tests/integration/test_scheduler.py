"""E3-S3: the scheduler enqueues a filings watch per universe company every 10 minutes and the
security-master load daily at 06:00 UTC, and does no work itself.

The universe comes from a security-master load of the committed fixtures, inside the test's
transaction.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from conftest import TEST_UNIVERSE, LoadMaster, connect_to

from marketdata.tasks import queue
from marketdata.tasks.kinds import FILINGS_WATCH, SECURITY_MASTER
from marketdata.tasks.scheduler import (
    build_scheduler,
    enqueue_security_master,
    enqueue_watches,
    run_scheduler,
)

Conn = psycopg.AsyncConnection[Any]
UNIVERSE_CIKS = sorted(set(TEST_UNIVERSE.values()))
LOCK = timedelta(minutes=5)


async def test_one_watch_per_universe_company(conn: Conn, load_master: LoadMaster) -> None:
    await load_master()

    tasks = await enqueue_watches(conn)

    assert sorted((task.kind, task.key) for task in tasks) == sorted(
        (FILINGS_WATCH, str(cik)) for cik in UNIVERSE_CIKS
    )
    assert all(task.payload == {"cik": int(task.key)} for task in tasks)
    assert all(task.status == "queued" for task in tasks)


async def test_a_finished_watch_runs_again_and_a_running_one_is_left(
    conn: Conn, load_master: LoadMaster
) -> None:
    await load_master()
    await enqueue_watches(conn)
    first = await queue.claim(conn, [FILINGS_WATCH], LOCK)
    second = await queue.claim(conn, [FILINGS_WATCH], LOCK)
    assert first is not None and second is not None
    await queue.complete(conn, first)

    again = {task.key: task for task in await enqueue_watches(conn)}

    assert (again[first.key].status, again[first.key].attempts) == ("queued", 0)
    assert (again[second.key].status, again[second.key].attempts) == ("running", 1)
    assert len(again) == len(UNIVERSE_CIKS)


async def test_the_daily_load_is_enqueued_once_per_day(conn: Conn) -> None:
    day = date(2026, 10, 25)
    task = await enqueue_security_master(conn, day)
    assert (task.kind, task.key, task.payload) == (
        SECURITY_MASTER,
        "2026-10-25",
        {"as_of": "2026-10-25"},
    )
    claimed = await queue.claim(conn, [SECURITY_MASTER], LOCK)
    assert claimed is not None
    await queue.fail(conn, claimed, "TaskRefused: the fund list would end …", permanent=True)

    again = await enqueue_security_master(conn, day)
    tomorrow = await enqueue_security_master(conn, day + timedelta(days=1))

    assert again.status == "dead"  # a refused load is not retried blindly
    assert (tomorrow.key, tomorrow.status) == ("2026-10-26", "queued")


def test_the_jobs_run_every_ten_minutes_and_daily_at_six_utc() -> None:
    def connect() -> Any:
        raise AssertionError("no job runs in this test")

    jobs = {job.id: job for job in build_scheduler(connect).get_jobs()}

    watch, load = jobs[FILINGS_WATCH], jobs[SECURITY_MASTER]
    assert watch.trigger.interval == timedelta(minutes=10)
    assert abs(watch.next_run_time - datetime.now(UTC)) < timedelta(minutes=1)  # at start
    before_six = datetime(2026, 10, 25, 5, 59, tzinfo=UTC)
    after_six = datetime(2026, 10, 25, 6, 0, 1, tzinfo=UTC)
    assert load.trigger.get_next_fire_time(None, before_six) == datetime(
        2026, 10, 25, 6, 0, tzinfo=UTC
    )
    assert load.trigger.get_next_fire_time(None, after_six) == datetime(
        2026, 10, 26, 6, 0, tzinfo=UTC
    )


async def _run(conn: Conn, now: datetime) -> dict[str, list[str]]:
    """Run the scheduler until its first watch round is queued; the tasks by kind."""
    stop = asyncio.Event()
    running = asyncio.create_task(run_scheduler(connect_to(conn), stop, now=now))
    async with asyncio.timeout(10):
        while len(await queue.list_tasks(conn, kind=FILINGS_WATCH)) < len(UNIVERSE_CIKS):
            await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(running, 10)
    by_kind: dict[str, list[str]] = {}
    for task in await queue.list_tasks(conn, limit=100):
        by_kind.setdefault(task.kind, []).append(task.key)
    return by_kind


async def test_started_after_six_it_enqueues_the_days_load_and_the_watches_at_once(
    conn: Conn, load_master: LoadMaster
) -> None:
    await load_master()

    by_kind = await _run(conn, now=datetime(2026, 10, 25, 7, 0, tzinfo=UTC))

    assert by_kind[SECURITY_MASTER] == ["2026-10-25"]
    assert sorted(by_kind[FILINGS_WATCH]) == sorted(str(cik) for cik in UNIVERSE_CIKS)


async def test_started_before_six_it_leaves_the_load_to_the_daily_job(
    conn: Conn, load_master: LoadMaster
) -> None:
    await load_master()

    by_kind = await _run(conn, now=datetime(2026, 10, 25, 5, 0, tzinfo=UTC))

    assert SECURITY_MASTER not in by_kind
    assert len(by_kind[FILINGS_WATCH]) == len(UNIVERSE_CIKS)
