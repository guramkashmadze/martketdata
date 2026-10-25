"""Scheduler process: enqueues periodic tasks and never does the work itself (E3-S3).

- Every 10 minutes, and once at start: one `filings_watch` task per universe company, keyed by
  its CIK (E9 adds the curated managers). They are enqueued with force, so a watch that
  finished, or died, runs again; one still queued, running or waiting to retry is left alone.
- Daily at 06:00 UTC: one `security_master` task keyed by the date. If the scheduler starts
  after 06:00, it enqueues the day's load at once; a load already done or dead that day is
  left as it is, so a refused load is not retried blindly (the next day's is a new task).

All work goes through the queue, so the worker is the only process that calls the SEC.
"""

import asyncio
import logging
import signal
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from psycopg import AsyncConnection

from marketdata.config import Settings
from marketdata.db.pool import create_pool
from marketdata.db.repositories import companies
from marketdata.db.repositories.tasks import Task
from marketdata.ingest.fetch import Connect
from marketdata.tasks import queue
from marketdata.tasks.kinds import FILINGS_WATCH, SECURITY_MASTER

WATCH_EVERY = timedelta(minutes=10)
SECURITY_MASTER_AT = time(6, 0, tzinfo=UTC)

log = structlog.get_logger(__name__)


async def enqueue_watches(conn: AsyncConnection[Any]) -> list[Task]:
    """One filings_watch per universe company; finished ones are queued again."""
    universe = await companies.list_in_universe(conn)
    return [
        await queue.enqueue(conn, FILINGS_WATCH, str(company.cik), {"cik": company.cik}, force=True)
        for company in universe
    ]


async def enqueue_security_master(conn: AsyncConnection[Any], day: date) -> Task:
    """The security-master load of `day`, once: a done or dead load stays as it is."""
    return await queue.enqueue(conn, SECURITY_MASTER, day.isoformat(), {"as_of": day.isoformat()})


def build_scheduler(connect: Connect, *, watch_every: timedelta = WATCH_EVERY) -> AsyncIOScheduler:
    async def watch_job() -> None:
        try:
            async with connect() as conn:
                tasks = await enqueue_watches(conn)
        except Exception as exc:  # the next run tries again
            log.error("scheduler_job_failed", job=FILINGS_WATCH, error=str(exc))
            return
        if not tasks:
            log.warning("no_universe", hint="run `marketdata ingest security-master` first")
        queued = sum(1 for task in tasks if task.status == "queued")
        log.info("watches_enqueued", companies=len(tasks), queued=queued)

    async def security_master_job() -> None:
        await _enqueue_load(connect, datetime.now(UTC).date())

    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        watch_job,
        IntervalTrigger(seconds=watch_every.total_seconds(), timezone=UTC),
        id=FILINGS_WATCH,
        next_run_time=datetime.now(UTC),  # and once at start
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        security_master_job,
        CronTrigger(hour=SECURITY_MASTER_AT.hour, minute=SECURITY_MASTER_AT.minute, timezone=UTC),
        id=SECURITY_MASTER,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler


async def _enqueue_load(connect: Connect, day: date) -> None:
    try:
        async with connect() as conn:
            task = await enqueue_security_master(conn, day)
    except Exception as exc:  # the next day's run tries again
        log.error("scheduler_job_failed", job=SECURITY_MASTER, error=str(exc))
        return
    log.info("security_master_enqueued", key=task.key, status=task.status)


async def run_scheduler(connect: Connect, stop: asyncio.Event, *, now: datetime) -> None:
    """Run the jobs until `stop` is set. Started after 06:00 UTC, enqueue the day's load."""
    scheduler = build_scheduler(connect)
    scheduler.start()
    log.info("scheduler_started", watch_every_minutes=WATCH_EVERY.total_seconds() / 60)
    try:
        if now.astimezone(UTC).timetz() >= SECURITY_MASTER_AT:
            await _enqueue_load(connect, now.astimezone(UTC).date())
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)  # runs on the loop's next pass
    await asyncio.sleep(0)
    log.info("scheduler_stopped")


async def serve(settings: Settings) -> None:
    """The `marketdata scheduler` process; SIGTERM and SIGINT (Ctrl-C) stop it."""
    # APScheduler logs every run at INFO; our own lines say what was enqueued.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for signum in signals:
        loop.add_signal_handler(signum, stop.set)
    try:
        async with create_pool(str(settings.database_url), max_size=2) as pool:
            await run_scheduler(pool.connection, stop, now=datetime.now(UTC))
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)
