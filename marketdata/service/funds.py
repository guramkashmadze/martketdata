"""Curated fund portfolios (E9)."""

from typing import Any

from psycopg import AsyncConnection

from marketdata.db.repositories import managers
from marketdata.db.repositories.managers import Manager


async def list_managers(conn: AsyncConnection[Any], *, curated: bool = True) -> list[Manager]:
    """The 13F managers by display name: the curated ones, or all with `curated=False`."""
    return await managers.list_managers(conn, curated=curated)
