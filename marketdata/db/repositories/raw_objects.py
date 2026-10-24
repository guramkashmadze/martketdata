"""The raw_objects table (PLAN.md §7): what was fetched, from where, and its sha256."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


class RawObject(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    url: str
    sha256: str
    size_bytes: int
    content_type: str | None
    fetched_at: datetime


_COLUMNS = "key, url, sha256, size_bytes, content_type, fetched_at"

# Raw objects are write-once, so the first row for a key stays: its fetched_at is when those
# bytes were first stored. Returns the new row, or the existing one.
_INSERT = f"""
    with inserted as (
      insert into raw_objects ({_COLUMNS})
      values (%(key)s, %(url)s, %(sha256)s, %(size_bytes)s, %(content_type)s, %(fetched_at)s)
      on conflict (key) do nothing
      returning {_COLUMNS}
    )
    select {_COLUMNS} from inserted
    union all
    select {_COLUMNS} from raw_objects where key = %(key)s
    limit 1
"""

_SELECT = f"select {_COLUMNS} from raw_objects where key = %(key)s"


async def upsert(conn: AsyncConnection[Any], row: RawObject) -> RawObject:
    """Insert the row unless the key exists. Returns the stored row either way."""
    async with conn.cursor(row_factory=class_row(RawObject)) as cur:
        await cur.execute(_INSERT, row.model_dump())
        stored = await cur.fetchone()
        if stored is None:
            # Another transaction inserted the key after this statement's snapshot.
            await cur.execute(_SELECT, {"key": row.key})
            stored = await cur.fetchone()
    if stored is None:
        raise RuntimeError(f"raw object {row.key} was neither inserted nor found")
    return stored


async def get(conn: AsyncConnection[Any], key: str) -> RawObject | None:
    async with conn.cursor(row_factory=class_row(RawObject)) as cur:
        await cur.execute(_SELECT, {"key": key})
        return await cur.fetchone()
