"""Async PostgreSQL connection pool (psycopg 3).

The pool is created closed. Open it with `await pool.open()`, which does not wait for the
database: a process can start while PostgreSQL is down, and /health then reports it.
"""

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]


def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> Pool:
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        # Replace connections that died (for example after a database restart) on checkout.
        check=AsyncConnectionPool.check_connection,
        name="marketdata",
    )
