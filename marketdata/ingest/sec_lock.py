"""One process calls the SEC at a time (D-007, D-044).

The SEC rate limit is a token bucket inside each process, so two processes calling the SEC at
once could together go over 8 requests per second and get the platform blocked. Every process
that calls the SEC holds a PostgreSQL advisory lock, the SEC slot, while it runs: the worker,
`marketdata sec get` and `marketdata ingest security-master`. A second one refuses to start
and says who holds the slot. The lock lives on its own connection and goes with it, so a
process that dies frees the slot. scripts/fetch_fixture.py has no database: don't run it while
the worker runs.

The worker in Docker Compose waits for the slot instead (`marketdata worker --wait`, D-137):
while a worker started by hand holds it, the container's looks again every 30 seconds and
takes over when that one stops, rather than exiting and being restarted in a loop.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import locks

RETRY_EVERY = 30.0  # seconds between two tries of a process that waits for the slot

log = structlog.get_logger(__name__)


class SecProcessBusy(Exception):
    """Another process holds the SEC slot."""


class SecWaitStopped(Exception):
    """A process stopped while it waited for the SEC slot."""


@asynccontextmanager
async def sec_process_lock(
    database_url: str,
    process: str,
    *,
    wait: bool = False,
    stop: asyncio.Event | None = None,
    retry_every: float = RETRY_EVERY,
) -> AsyncIterator[None]:
    """Hold the SEC slot while the block runs; raises SecProcessBusy if another process has it.

    `process` names this process in pg_stat_activity, so a refused process can say who holds
    the slot. With `wait`, a busy slot is tried again every `retry_every` seconds until it is
    free; `stop` set meanwhile raises SecWaitStopped.
    """
    async with await AsyncConnection.connect(
        database_url, autocommit=True, application_name=process
    ) as conn:
        holder_seen: int | None = None
        while not await locks.try_sec_slot(conn):
            holder = await locks.sec_slot_holder(conn)
            if not wait:
                raise SecProcessBusy(_busy_message(holder))
            if holder is None or holder.pid != holder_seen:
                log.warning(
                    "sec_slot_busy",
                    holder=_holder_text(holder),
                    retry_seconds=retry_every,
                    hint="this process starts when that one stops",
                )
                holder_seen = holder.pid if holder else None
            if await _stopped(stop, retry_every):
                raise SecWaitStopped(f"{process} stopped while it waited for the SEC slot")
        if holder_seen is not None:
            log.info("sec_slot_taken", process=process)
        yield


async def _stopped(stop: asyncio.Event | None, seconds: float) -> bool:
    """Wait `seconds`, or less when `stop` is set; True if it is."""
    if stop is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True


def _holder_text(holder: locks.LockHolder | None) -> str:
    if holder is None:
        return "another process"
    name = holder.application_name or "a process"
    since = (
        f", connected {holder.backend_start.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
        if holder.backend_start
        else ""
    )
    return f"'{name}' (PostgreSQL pid {holder.pid}{since})"


def _busy_message(holder: locks.LockHolder | None) -> str:
    return (
        f"Another process is calling the SEC: {_holder_text(holder)}. One process at a time "
        "keeps the platform under the SEC's rate limit: wait for it to finish or stop it, or "
        "let the worker do this work."
    )
