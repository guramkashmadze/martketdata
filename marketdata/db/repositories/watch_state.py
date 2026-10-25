"""The watch_state table (E3-S4, D-059): the last submissions file each company's watch
processed, so an unchanged file is neither stored again nor parsed."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


class WatchState(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int
    sha256: str
    raw_key: str
    checked_at: datetime
    changed_at: datetime


_COLUMNS = "cik, sha256, raw_key, checked_at, changed_at"

# Held by a watch or backfill of one company while it writes, so two never interleave.
_LOCK = "select pg_advisory_xact_lock(hashtextextended('marketdata.watch:' || %(cik)s, 0))"

_GET = f"select {_COLUMNS} from watch_state where cik = %(cik)s"

_RECORD_CHANGE = """
    insert into watch_state (cik, sha256, raw_key, checked_at, changed_at)
    values (%(cik)s, %(sha256)s, %(raw_key)s, %(at)s, %(at)s)
    on conflict (cik) do update set
      sha256 = excluded.sha256,
      raw_key = excluded.raw_key,
      checked_at = excluded.checked_at,
      changed_at = excluded.changed_at
"""

_RECORD_CHECK = "update watch_state set checked_at = %(at)s where cik = %(cik)s"


async def lock(conn: AsyncConnection[Any], cik: int) -> None:
    """Wait until no other transaction writes this company's filings; released at commit."""
    await conn.execute(_LOCK, {"cik": str(cik)})


async def get(conn: AsyncConnection[Any], cik: int) -> WatchState | None:
    async with conn.cursor(row_factory=class_row(WatchState)) as cur:
        await cur.execute(_GET, {"cik": cik})
        return await cur.fetchone()


async def record_change(
    conn: AsyncConnection[Any], cik: int, sha256: str, raw_key: str, at: datetime
) -> None:
    """A new file was processed at `at`."""
    params = {"cik": cik, "sha256": sha256, "raw_key": raw_key, "at": at}
    await conn.execute(_RECORD_CHANGE, params)


async def record_check(conn: AsyncConnection[Any], cik: int, at: datetime) -> None:
    """The file was fetched at `at` and had not changed."""
    await conn.execute(_RECORD_CHECK, {"cik": cik, "at": at})
