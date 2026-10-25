"""PostgreSQL advisory locks that coordinate processes (E3-S2).

The SEC slot is a session lock with two int4 keys, so pg_locks shows it as (classid, objid)
with objsubid 2. Advisory locks belong to one database, so the test database never blocks the
dev database.
"""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

SEC_SLOT = (0x6D64, 1)  # 'md', 1
_SEC_SLOT = {"k1": SEC_SLOT[0], "k2": SEC_SLOT[1]}

_TRY_SEC_SLOT = "select pg_try_advisory_lock(%(k1)s::int, %(k2)s::int)"

_SEC_SLOT_HOLDER = """
    select a.pid, a.application_name, a.backend_start
    from pg_locks l join pg_stat_activity a on a.pid = l.pid
    where l.locktype = 'advisory' and l.granted
      and l.database = (select oid from pg_database where datname = current_database())
      and l.classid::bigint = %(k1)s and l.objid::bigint = %(k2)s and l.objsubid = 2
"""


class LockHolder(BaseModel):
    model_config = ConfigDict(frozen=True)

    pid: int
    application_name: str | None
    backend_start: datetime | None


async def try_sec_slot(conn: AsyncConnection[Any]) -> bool:
    """Take the SEC slot for this session; False if another session holds it."""
    async with conn.cursor() as cur:
        await cur.execute(_TRY_SEC_SLOT, _SEC_SLOT)
        row = await cur.fetchone()
    return bool(row and row[0])


async def sec_slot_holder(conn: AsyncConnection[Any]) -> LockHolder | None:
    async with conn.cursor(row_factory=class_row(LockHolder)) as cur:
        await cur.execute(_SEC_SLOT_HOLDER, _SEC_SLOT)
        return await cur.fetchone()
