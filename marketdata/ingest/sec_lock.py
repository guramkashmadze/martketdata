"""One process calls the SEC at a time (D-007, D-044).

The SEC rate limit is a token bucket inside each process, so two processes calling the SEC at
once could together go over 8 requests per second and get the platform blocked. Every process
that calls the SEC holds a PostgreSQL advisory lock, the SEC slot, while it runs: the worker,
`marketdata sec get` and `marketdata ingest security-master`. A second one refuses to start
and says who holds the slot. The lock lives on its own connection and goes with it, so a
process that dies frees the slot. scripts/fetch_fixture.py has no database: don't run it while
the worker runs.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC

from psycopg import AsyncConnection

from marketdata.db.repositories import locks


class SecProcessBusy(Exception):
    """Another process holds the SEC slot."""


@asynccontextmanager
async def sec_process_lock(database_url: str, process: str) -> AsyncIterator[None]:
    """Hold the SEC slot while the block runs; raises SecProcessBusy if another process has it.

    `process` names this process in pg_stat_activity, so a refused process can say who holds
    the slot.
    """
    async with await AsyncConnection.connect(
        database_url, autocommit=True, application_name=process
    ) as conn:
        if not await locks.try_sec_slot(conn):
            raise SecProcessBusy(_busy_message(await locks.sec_slot_holder(conn)))
        yield


def _busy_message(holder: locks.LockHolder | None) -> str:
    if holder is None:
        who = "another process"
    else:
        name = holder.application_name or "a process"
        since = (
            f", connected {holder.backend_start.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
            if holder.backend_start
            else ""
        )
        who = f"'{name}' (PostgreSQL pid {holder.pid}{since})"
    return (
        f"Another process is calling the SEC: {who}. One process at a time keeps the platform "
        "under the SEC's rate limit: wait for it to finish or stop it, or let the worker do "
        "this work."
    )
