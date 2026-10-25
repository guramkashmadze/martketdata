"""Enqueue, claim, retry and reclaim tasks (E3-S1).

The queue is the tasks table (PLAN.md §7), so it needs no other infrastructure. A task is unique
by (kind, key):

- `enqueue` adds a task once. Enqueueing it again returns it unchanged while it is queued,
  running or waiting to retry. A finished task (done or dead) runs again only with
  `force=True`, which queues it again with the new payload and a fresh attempt count. Periodic
  tasks (one watch per company, one security-master load per day) are enqueued with force.
- `claim` gives the oldest ready task of the worker's kinds to one worker (FOR UPDATE SKIP
  LOCKED) and locks it for a while; a task whose lock ran out (its worker died) is ready again.
- `fail` retries after the kind's backoff, or marks the task dead after its last attempt or when
  the handler refused it for good.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

from marketdata.db.repositories import tasks
from marketdata.db.repositories.tasks import Task, TaskCount, TaskStatus


@dataclass(frozen=True)
class RetryPolicy:
    """How often a kind is attempted, and how long to wait after each failed attempt.

    `backoff[n - 1]` is the wait after attempt n fails; the last wait repeats if attempts
    outnumber it. Webhook delivery (E5b) uses 3 attempts, about 1 and 10 minutes apart (§9.11).
    """

    max_attempts: int = 5
    backoff: tuple[timedelta, ...] = (
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=60),
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_attempts > 1 and not self.backoff:
            raise ValueError("a kind that retries needs at least one backoff")

    def retry_in(self, attempts: int) -> timedelta | None:
        """The wait before the next attempt after `attempts` failed ones; None: give up."""
        if attempts >= self.max_attempts:
            return None
        return self.backoff[min(attempts, len(self.backoff)) - 1]


DEFAULT_RETRY = RetryPolicy()


async def enqueue(
    conn: AsyncConnection[Any],
    kind: str,
    key: str,
    payload: Mapping[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    force: bool = False,
) -> Task:
    """Queue a task once per (kind, key); `force` queues a done or dead task again."""
    return await tasks.enqueue(conn, kind, key, payload, run_after=run_after, force=force)


async def claim(
    conn: AsyncConnection[Any], kinds: Sequence[str], lock_for: timedelta
) -> Task | None:
    """The oldest ready task of these kinds, running and locked for `lock_for`; None if none."""
    if not kinds:
        return None
    return await tasks.claim(conn, kinds, lock_for)


async def complete(conn: AsyncConnection[Any], task: Task) -> Task | None:
    """Mark the claimed attempt done; None when another worker holds the task now."""
    return await tasks.complete(conn, task.task_id, task.attempts)


async def fail(
    conn: AsyncConnection[Any],
    task: Task,
    error: str,
    policy: RetryPolicy = DEFAULT_RETRY,
    *,
    permanent: bool = False,
) -> Task | None:
    """Record the claimed attempt as failed: retry later, or dead after the last attempt or
    when `permanent`. None when another worker holds the task now."""
    retry_in = None if permanent else policy.retry_in(task.attempts)
    return await tasks.fail(conn, task.task_id, task.attempts, error, retry_in=retry_in)


async def release(conn: AsyncConnection[Any], task: Task) -> Task | None:
    """Put a claimed task back without counting the attempt (the worker is stopping)."""
    return await tasks.release(conn, task.task_id, task.attempts)


async def extend_lock(conn: AsyncConnection[Any], task: Task, lock_for: timedelta) -> bool:
    """Renew the lock of a claimed task; False when another worker holds it now."""
    return await tasks.extend_lock(conn, task.task_id, task.attempts, lock_for)


async def list_tasks(
    conn: AsyncConnection[Any],
    *,
    status: TaskStatus | None = None,
    kind: str | None = None,
    limit: int = 50,
) -> list[Task]:
    return await tasks.list_tasks(conn, status=status, kind=kind, limit=limit)


async def count(conn: AsyncConnection[Any]) -> list[TaskCount]:
    return await tasks.count(conn)


async def retry(conn: AsyncConnection[Any], task_ids: Sequence[int]) -> list[Task]:
    """Queue these dead or failed tasks again now; other tasks are left as they are."""
    return await tasks.retry(conn, task_ids)


async def retry_dead(conn: AsyncConnection[Any], *, kind: str | None = None) -> list[Task]:
    return await tasks.retry_dead(conn, kind=kind)


async def get_many(conn: AsyncConnection[Any], task_ids: Sequence[int]) -> list[Task]:
    return await tasks.get_many(conn, task_ids)


async def purge_done(conn: AsyncConnection[Any], older_than: timedelta) -> int:
    """Delete done tasks unchanged for `older_than`. Periodic tasks are reused, so they stay."""
    return await tasks.purge_done(conn, older_than)
