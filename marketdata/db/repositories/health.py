"""Database liveness check for /health."""

from typing import Any

from psycopg import AsyncConnection


async def ping(conn: AsyncConnection[Any]) -> None:
    await conn.execute("select 1")
