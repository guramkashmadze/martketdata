"""The tasks table (PLAN.md §7): the work queue in PostgreSQL (E0-S3, E3-S1).

A task is unique by (kind, key). `claim` gives each ready task to one worker: it takes the oldest
ready row with FOR UPDATE SKIP LOCKED, marks it running and counts the attempt. The attempt count
also fences the worker holding a task: `complete`, `fail`, `release` and `extend_lock` change the
row only while it is still running under the attempt they were given, so a worker whose lock ran
out, and whose task another worker claimed, changes nothing.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

TaskStatus = Literal["queued", "running", "done", "failed", "dead"]
TASK_STATUSES: tuple[TaskStatus, ...] = ("queued", "running", "done", "failed", "dead")


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


class TaskCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    status: TaskStatus
    count: int


_COLUMNS = """
    task_id, kind, key, payload, status, attempts, run_after, locked_until, last_error,
    created_at, updated_at
"""

# A new task is queued. An existing one is returned unchanged, unless `force` is set and it has
# finished (done or dead): then it is queued again with the new payload. A task that is queued,
# running or waiting to retry is never reset, so a periodic enqueue can't start it twice.
_ENQUEUE = f"""
    insert into tasks (kind, key, payload, run_after)
    values (%(kind)s, %(key)s, %(payload)s, coalesce(%(run_after)s, now()))
    on conflict (kind, key) do update set
      payload = excluded.payload,
      status = 'queued',
      attempts = 0,
      run_after = excluded.run_after,
      locked_until = null,
      last_error = null,
      updated_at = now()
    where %(force)s and tasks.status in ('done', 'dead')
    returning {_COLUMNS}
"""

_SELECT_BY_KEY = f"select {_COLUMNS} from tasks where kind = %(kind)s and key = %(key)s"

_SELECT_BY_ID = f"select {_COLUMNS} from tasks where task_id = %(task_id)s"

_SELECT_BY_IDS = f"select {_COLUMNS} from tasks where task_id = any(%(ids)s) order by task_id"

# Ready: queued or waiting to retry, and due; or running with a lock that ran out (the worker
# died). Oldest first.
_CLAIM = f"""
    update tasks set
      status = 'running',
      attempts = attempts + 1,
      locked_until = now() + %(lock_for)s,
      updated_at = now()
    where task_id = (
      select task_id from tasks
      where kind = any(%(kinds)s)
        and (
          (status in ('queued', 'failed') and run_after <= now())
          or (status = 'running' and locked_until < now())
        )
      order by run_after, task_id
      for update skip locked
      limit 1
    )
    returning {_COLUMNS}
"""

_FENCE = "task_id = %(task_id)s and attempts = %(attempts)s and status = 'running'"

_COMPLETE = f"""
    update tasks set status = 'done', locked_until = null, last_error = null, updated_at = now()
    where {_FENCE}
    returning {_COLUMNS}
"""

_RETRY_LATER = f"""
    update tasks set
      status = 'failed',
      run_after = now() + %(retry_in)s,
      locked_until = null,
      last_error = %(error)s,
      updated_at = now()
    where {_FENCE}
    returning {_COLUMNS}
"""

_GIVE_UP = f"""
    update tasks set status = 'dead', locked_until = null, last_error = %(error)s,
      updated_at = now()
    where {_FENCE}
    returning {_COLUMNS}
"""

# Back in the queue without counting the attempt (the worker stopped before finishing it).
_RELEASE = f"""
    update tasks set status = 'queued', attempts = attempts - 1, locked_until = null,
      updated_at = now()
    where {_FENCE}
    returning {_COLUMNS}
"""

# A heartbeat: updated_at stays the time of the last change of state.
_EXTEND_LOCK = f"""
    update tasks set locked_until = now() + %(lock_for)s
    where {_FENCE}
    returning task_id
"""

_LIST = f"""
    select {_COLUMNS} from tasks
    where (%(status)s::text is null or status = %(status)s)
      and (%(kind)s::text is null or kind = %(kind)s)
    order by updated_at desc, task_id desc
    limit %(limit)s
"""

_COUNT = "select kind, status, count(*) as count from tasks group by kind, status order by kind"

_RETRY_IDS = f"""
    update tasks set status = 'queued', attempts = 0, run_after = now(), locked_until = null,
      updated_at = now()
    where task_id = any(%(ids)s) and status in ('dead', 'failed')
    returning {_COLUMNS}
"""

_RETRY_DEAD = f"""
    update tasks set status = 'queued', attempts = 0, run_after = now(), locked_until = null,
      updated_at = now()
    where status = 'dead' and (%(kind)s::text is null or kind = %(kind)s)
    returning {_COLUMNS}
"""

_PURGE_DONE = "delete from tasks where status = 'done' and updated_at < now() - %(older_than)s"


async def enqueue(
    conn: AsyncConnection[Any],
    kind: str,
    key: str,
    payload: Mapping[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    force: bool = False,
) -> Task:
    """Queue a task, or return the existing (kind, key) task; see _ENQUEUE for `force`."""
    params = {
        "kind": kind,
        "key": key,
        "payload": Jsonb(dict(payload or {})),
        "run_after": run_after,
        "force": force,
    }
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_ENQUEUE, params)
        task = await cur.fetchone()
        if task is None:
            # It exists and was left as it is. If another transaction inserted it after this
            # statement's snapshot was taken, it is visible to a new statement.
            await cur.execute(_SELECT_BY_KEY, params)
            task = await cur.fetchone()
    if task is None:
        raise RuntimeError(f"task {kind}:{key} was neither inserted nor found")
    return task


async def get(conn: AsyncConnection[Any], task_id: int) -> Task | None:
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_SELECT_BY_ID, {"task_id": task_id})
        return await cur.fetchone()


async def get_many(conn: AsyncConnection[Any], task_ids: Sequence[int]) -> list[Task]:
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_SELECT_BY_IDS, {"ids": list(task_ids)})
        return await cur.fetchall()


async def claim(
    conn: AsyncConnection[Any], kinds: Sequence[str], lock_for: timedelta
) -> Task | None:
    """The oldest ready task of these kinds, now running under a lock of `lock_for`."""
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_CLAIM, {"kinds": list(kinds), "lock_for": lock_for})
        return await cur.fetchone()


async def complete(conn: AsyncConnection[Any], task_id: int, attempts: int) -> Task | None:
    """Mark the attempt done. None when the task is no longer held under that attempt."""
    return await _fenced(conn, _COMPLETE, {"task_id": task_id, "attempts": attempts})


async def fail(
    conn: AsyncConnection[Any],
    task_id: int,
    attempts: int,
    error: str,
    *,
    retry_in: timedelta | None,
) -> Task | None:
    """Record a failed attempt: retry after `retry_in`, or give up (dead) when it is None."""
    params = {"task_id": task_id, "attempts": attempts, "error": error, "retry_in": retry_in}
    return await _fenced(conn, _GIVE_UP if retry_in is None else _RETRY_LATER, params)


async def release(conn: AsyncConnection[Any], task_id: int, attempts: int) -> Task | None:
    """Put a running task back in the queue without counting the attempt."""
    return await _fenced(conn, _RELEASE, {"task_id": task_id, "attempts": attempts})


async def extend_lock(
    conn: AsyncConnection[Any], task_id: int, attempts: int, lock_for: timedelta
) -> bool:
    """Renew the lock; False when the task is no longer held under that attempt."""
    params = {"task_id": task_id, "attempts": attempts, "lock_for": lock_for}
    async with conn.cursor() as cur:
        await cur.execute(_EXTEND_LOCK, params)
        return await cur.fetchone() is not None


async def list_tasks(
    conn: AsyncConnection[Any],
    *,
    status: TaskStatus | None = None,
    kind: str | None = None,
    limit: int = 50,
) -> list[Task]:
    """Most recently changed first."""
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_LIST, {"status": status, "kind": kind, "limit": limit})
        return await cur.fetchall()


async def count(conn: AsyncConnection[Any]) -> list[TaskCount]:
    async with conn.cursor(row_factory=class_row(TaskCount)) as cur:
        await cur.execute(_COUNT)
        return await cur.fetchall()


async def retry(conn: AsyncConnection[Any], task_ids: Sequence[int]) -> list[Task]:
    """Queue these tasks again now with a fresh attempt count, if they are dead or failed."""
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_RETRY_IDS, {"ids": list(task_ids)})
        return await cur.fetchall()


async def retry_dead(conn: AsyncConnection[Any], *, kind: str | None = None) -> list[Task]:
    """Queue every dead task (of one kind) again now with a fresh attempt count."""
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(_RETRY_DEAD, {"kind": kind})
        return await cur.fetchall()


async def purge_done(conn: AsyncConnection[Any], older_than: timedelta) -> int:
    """Delete done tasks last changed more than `older_than` ago; returns how many."""
    async with conn.cursor() as cur:
        await cur.execute(_PURGE_DONE, {"older_than": older_than})
        return cur.rowcount


async def _fenced(conn: AsyncConnection[Any], query: str, params: dict[str, Any]) -> Task | None:
    async with conn.cursor(row_factory=class_row(Task)) as cur:
        await cur.execute(query, params)
        return await cur.fetchone()
