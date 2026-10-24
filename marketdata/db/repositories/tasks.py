"""The tasks table (PLAN.md §7). Queue semantics (claim, retry, reclaim) are added in E3."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

TaskStatus = Literal["queued", "running", "done", "failed", "dead"]


class Task(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: int
    kind: str
    key: str
    payload: dict[str, Any]
    status: TaskStatus
    attempts: int
    run_after: datetime
    locked_until: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


_COLUMNS = """
    task_id, kind, key, payload, status, attempts, run_after, locked_until, last_error,
    created_at, updated_at
"""

# Returns the new row, or the existing row when (kind, key) is already there.
_INSERT = f"""
    with inserted as (
      insert into tasks (kind, key, payload)
      values (%(kind)s, %(key)s, %(payload)s)
      on conflict (kind, key) do nothing
      returning {_COLUMNS}
    )
    select {_COLUMNS} from inserted
    union all
    select {_COLUMNS} from tasks where kind = %(kind)s and key = %(key)s
    limit 1
"""

_SELECT_BY_KEY = f"select {_COLUMNS} from tasks where kind = %(kind)s and key = %(key)s"

_SELECT_BY_ID = f"select {_COLUMNS} from tasks where task_id = %(task_id)s"


async def insert(
    conn: AsyncConnection[Any],
    kind: str,
    key: str,
    payload: Mapping[str, Any] | None = None,
) -> Task:
    """Insert a queued task. If (kind, key) exists, that row is returned unchanged."""
    params = {"kind": kind, "key": key, "payload": Jsonb(dict(payload or {}))}
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_INSERT, params)
        task = await cur.fetchone()
        if task is None:
            # Another transaction inserted the same (kind, key) after this statement's
            # snapshot was taken; it is visible to a new statement.
            await cur.execute(_SELECT_BY_KEY, params)
            task = await cur.fetchone()
    if task is None:
        raise RuntimeError(f"task {kind}:{key} was neither inserted nor found")
    return task


async def get(conn: AsyncConnection[Any], task_id: int) -> Task | None:
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_SELECT_BY_ID, {"task_id": task_id})
        return await cur.fetchone()
