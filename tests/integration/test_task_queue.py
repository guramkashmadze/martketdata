"""E3-S1: the task queue in PostgreSQL.

Most tests run inside the test's transaction, where now() stands still: a task due at now() is
ready, a backoff of a minute keeps it waiting, and a lock that ended a second before now() has
run out. The concurrency tests need rows other connections can see, so they commit and clean up.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from marketdata.tasks import queue
from marketdata.tasks.queue import RetryPolicy

LOCK = timedelta(minutes=5)
EXPIRED = timedelta(seconds=-1)
NOW = timedelta(0)
# Retries become due at once, so one transaction can run every attempt.
IMMEDIATE = RetryPolicy(max_attempts=5, backoff=(NOW,))

Conn = psycopg.AsyncConnection[Any]


async def test_enqueue_dedupes_while_a_task_is_pending(conn: Conn) -> None:
    first = await queue.enqueue(conn, "filings_watch", "320193", {"cik": 320193})
    again = await queue.enqueue(conn, "filings_watch", "320193", {"cik": 1}, force=True)

    assert again == first  # queued: even force leaves it as it is


async def test_a_done_task_runs_again_only_with_force(conn: Conn) -> None:
    await queue.enqueue(conn, "filings_backfill", "320193", {"years": 5})
    claimed = await queue.claim(conn, ["filings_backfill"], LOCK)
    assert claimed is not None
    done = await queue.complete(conn, claimed)
    assert done is not None and done.status == "done"

    unchanged = await queue.enqueue(conn, "filings_backfill", "320193", {"years": 10})
    assert unchanged == done

    again = await queue.enqueue(conn, "filings_backfill", "320193", {"years": 10}, force=True)
    assert again.task_id == done.task_id
    assert (again.status, again.attempts, again.payload) == ("queued", 0, {"years": 10})
    assert again.locked_until is None and again.last_error is None


async def test_force_requeues_a_dead_task_but_not_a_failed_or_running_one(conn: Conn) -> None:
    for key in ("dead", "failed", "running"):
        await queue.enqueue(conn, "k", key)
    claimed = {}
    while (task := await queue.claim(conn, ["k"], LOCK)) is not None:
        claimed[task.key] = task
    await queue.fail(conn, claimed["dead"], "boom", permanent=True)
    failed = await queue.fail(conn, claimed["failed"], "boom")

    revived = await queue.enqueue(conn, "k", "dead", force=True)
    kept_failed = await queue.enqueue(conn, "k", "failed", force=True)
    kept_running = await queue.enqueue(conn, "k", "running", force=True)

    assert (revived.status, revived.attempts, revived.last_error) == ("queued", 0, None)
    assert kept_failed == failed
    assert (kept_running.status, kept_running.attempts) == ("running", 1)


async def test_claim_takes_the_oldest_ready_task_of_the_given_kinds(conn: Conn) -> None:
    first = await queue.enqueue(conn, "a", "1")
    await queue.enqueue(conn, "b", "2")
    await queue.enqueue(conn, "a", "3")

    claimed = await queue.claim(conn, ["a"], LOCK)

    assert claimed is not None and claimed.task_id == first.task_id
    assert (claimed.status, claimed.attempts) == ("running", 1)
    assert claimed.locked_until is not None
    assert claimed.locked_until - claimed.updated_at == LOCK
    assert await queue.claim(conn, [], LOCK) is None
    third = await queue.claim(conn, ["a"], LOCK)
    assert third is not None and third.key == "3"
    assert await queue.claim(conn, ["a"], LOCK) is None  # "b" isn't asked for


async def test_claim_takes_live_work_before_periodic_work_before_backfills(conn: Conn) -> None:
    backfill = await queue.enqueue(conn, "k", "backfill", priority=queue.PRIORITY_BACKFILL)
    periodic = await queue.enqueue(conn, "k", "periodic")
    live = await queue.enqueue(conn, "k", "live", priority=queue.PRIORITY_LIVE)
    older_live = await queue.enqueue(conn, "k", "older", priority=queue.PRIORITY_LIVE)
    await conn.execute(
        "update tasks set run_after = run_after - interval '1 hour' where task_id = %s",
        [older_live.task_id],
    )

    claimed = [await queue.claim(conn, ["k"], LOCK) for _ in range(4)]

    assert [task.key if task else None for task in claimed] == [
        "older",
        "live",
        "periodic",
        "backfill",
    ]
    assert (backfill.priority, periodic.priority, live.priority) == (-10, 0, 10)


async def test_a_waiting_task_enqueued_again_at_a_higher_priority_is_raised(conn: Conn) -> None:
    await queue.enqueue(conn, "earnings_extract", "a", priority=queue.PRIORITY_BACKFILL)

    raised = await queue.enqueue(conn, "earnings_extract", "a", priority=queue.PRIORITY_LIVE)
    lower = await queue.enqueue(conn, "earnings_extract", "a", priority=queue.PRIORITY_BACKFILL)

    assert (raised.priority, lower.priority) == (queue.PRIORITY_LIVE, queue.PRIORITY_LIVE)


async def test_force_queues_a_done_task_again_at_its_new_priority(conn: Conn) -> None:
    await queue.enqueue(conn, "earnings_extract", "a", priority=queue.PRIORITY_LIVE)
    claimed = await queue.claim(conn, ["earnings_extract"], LOCK)
    assert claimed is not None
    await queue.complete(conn, claimed)

    again = await queue.enqueue(
        conn, "earnings_extract", "a", force=True, priority=queue.PRIORITY_BACKFILL
    )

    assert (again.status, again.priority) == ("queued", queue.PRIORITY_BACKFILL)


async def test_a_task_due_later_waits(conn: Conn) -> None:
    created = await queue.enqueue(conn, "a", "1")
    await queue.enqueue(conn, "a", "2", run_after=created.run_after + timedelta(minutes=1))

    claimed = await queue.claim(conn, ["a"], LOCK)

    assert claimed is not None and claimed.key == "1"
    assert await queue.claim(conn, ["a"], LOCK) is None


async def test_a_task_whose_lock_ran_out_is_claimed_again(conn: Conn) -> None:
    await queue.enqueue(conn, "a", "1")
    lost = await queue.claim(conn, ["a"], EXPIRED)  # its worker died
    assert lost is not None

    reclaimed = await queue.claim(conn, ["a"], LOCK)

    assert reclaimed is not None and reclaimed.task_id == lost.task_id
    assert reclaimed.attempts == 2
    assert await queue.claim(conn, ["a"], LOCK) is None  # a live lock is respected


async def test_the_old_holder_of_a_reclaimed_task_changes_nothing(conn: Conn) -> None:
    await queue.enqueue(conn, "a", "1")
    lost = await queue.claim(conn, ["a"], EXPIRED)
    assert lost is not None
    holder = await queue.claim(conn, ["a"], LOCK)
    assert holder is not None

    assert await queue.complete(conn, lost) is None
    assert await queue.fail(conn, lost, "late") is None
    assert await queue.release(conn, lost) is None
    assert await queue.extend_lock(conn, lost, LOCK) is False
    assert await queue.extend_lock(conn, holder, LOCK) is True
    done = await queue.complete(conn, holder)
    assert done is not None and done.status == "done"


async def test_a_failed_attempt_waits_for_its_backoff(conn: Conn) -> None:
    await queue.enqueue(conn, "a", "1")
    claimed = await queue.claim(conn, ["a"], LOCK)
    assert claimed is not None

    failed = await queue.fail(conn, claimed, "SecUnavailable: HTTP 503")

    assert failed is not None
    assert (failed.status, failed.attempts, failed.locked_until) == ("failed", 1, None)
    assert failed.last_error == "SecUnavailable: HTTP 503"
    assert failed.run_after - failed.updated_at == timedelta(minutes=1)
    assert await queue.claim(conn, ["a"], LOCK) is None


async def test_a_task_is_dead_after_its_last_attempt(conn: Conn) -> None:
    await queue.enqueue(conn, "a", "1")
    statuses = []
    while (task := await queue.claim(conn, ["a"], LOCK)) is not None:
        failed = await queue.fail(conn, task, f"attempt {task.attempts}", IMMEDIATE)
        assert failed is not None
        statuses.append(failed.status)

    assert statuses == ["failed"] * 4 + ["dead"]
    dead = (await queue.list_tasks(conn, kind="a"))[0]
    assert (dead.status, dead.attempts, dead.last_error) == ("dead", 5, "attempt 5")


async def test_a_kind_with_three_attempts_dies_after_the_third(conn: Conn) -> None:
    webhook = RetryPolicy(max_attempts=3, backoff=(NOW,))
    await queue.enqueue(conn, "webhook_deliver", "event:1")
    attempts = 0
    while (task := await queue.claim(conn, ["webhook_deliver"], LOCK)) is not None:
        attempts = task.attempts
        await queue.fail(conn, task, "HTTP 500", webhook)

    assert attempts == 3


async def test_a_refused_task_is_dead_at_once(conn: Conn) -> None:
    await queue.enqueue(conn, "security_master", "2026-10-25")
    claimed = await queue.claim(conn, ["security_master"], LOCK)
    assert claimed is not None

    dead = await queue.fail(conn, claimed, "SnapshotRefused: …", permanent=True)

    assert dead is not None and (dead.status, dead.attempts) == ("dead", 1)


async def test_release_puts_a_task_back_without_counting_the_attempt(conn: Conn) -> None:
    await queue.enqueue(conn, "a", "1")
    claimed = await queue.claim(conn, ["a"], LOCK)
    assert claimed is not None

    released = await queue.release(conn, claimed)

    assert released is not None
    assert (released.status, released.attempts, released.locked_until) == ("queued", 0, None)
    again = await queue.claim(conn, ["a"], LOCK)
    assert again is not None and again.attempts == 1


async def test_retry_queues_dead_and_failed_tasks_only(conn: Conn) -> None:
    for key in ("dead", "failed", "done"):
        await queue.enqueue(conn, "a", key)
    claimed = {}
    while (task := await queue.claim(conn, ["a"], LOCK)) is not None:
        claimed[task.key] = task
    await queue.fail(conn, claimed["dead"], "boom", permanent=True)
    await queue.fail(conn, claimed["failed"], "boom")
    await queue.complete(conn, claimed["done"])
    ids = [claimed[key].task_id for key in ("dead", "failed", "done")]

    retried = await queue.retry(conn, ids)

    assert sorted(task.key for task in retried) == ["dead", "failed"]
    assert all((t.status, t.attempts, t.locked_until) == ("queued", 0, None) for t in retried)
    assert all(t.last_error == "boom" for t in retried)  # kept until an attempt succeeds
    assert [t.status for t in await queue.get_many(conn, ids)] == ["queued", "queued", "done"]


async def test_retry_dead_by_kind(conn: Conn) -> None:
    for kind in ("a", "b"):
        await queue.enqueue(conn, kind, "1")
        task = await queue.claim(conn, [kind], LOCK)
        assert task is not None
        await queue.fail(conn, task, "boom", permanent=True)

    retried = await queue.retry_dead(conn, kind="a")

    assert [(t.kind, t.status) for t in retried] == [("a", "queued")]
    assert [(t.kind, t.status) for t in await queue.retry_dead(conn)] == [("b", "queued")]


async def test_purge_done_deletes_only_old_done_tasks(conn: Conn) -> None:
    for key in ("old", "recent", "old-but-dead"):
        await queue.enqueue(conn, "a", key)
    claimed = {}
    while (task := await queue.claim(conn, ["a"], LOCK)) is not None:
        claimed[task.key] = task
    await queue.complete(conn, claimed["old"])
    await queue.complete(conn, claimed["recent"])
    await queue.fail(conn, claimed["old-but-dead"], "boom", permanent=True)
    await conn.execute(
        "update tasks set updated_at = now() - interval '8 days' where key like %s", ["old%"]
    )

    deleted = await queue.purge_done(conn, timedelta(days=7))

    assert deleted == 1
    assert sorted(t.key for t in await queue.list_tasks(conn, kind="a")) == [
        "old-but-dead",
        "recent",
    ]


async def test_list_and_count(conn: Conn) -> None:
    await queue.enqueue(conn, "a", "1")
    await queue.enqueue(conn, "a", "2")
    await queue.enqueue(conn, "b", "1")
    claimed = await queue.claim(conn, ["b"], LOCK)
    assert claimed is not None

    counts = {(c.kind, c.status): c.count for c in await queue.count(conn) if c.kind in ("a", "b")}
    running = await queue.list_tasks(conn, status="running")
    queued_a = await queue.list_tasks(conn, status="queued", kind="a")

    assert counts == {("a", "queued"): 2, ("b", "running"): 1}
    assert [(t.kind, t.key) for t in running] == [("b", "1")]
    assert sorted(t.key for t in queued_a) == ["1", "2"]
    assert len(await queue.list_tasks(conn, limit=2)) == 2


# --- Two workers at once: committed rows, separate connections.


@pytest.fixture
async def shared_kind(migrated_database: str) -> AsyncIterator[str]:
    kind = f"test-claim-{uuid4().hex[:8]}"
    yield kind
    async with await psycopg.AsyncConnection.connect(migrated_database, autocommit=True) as conn:
        await conn.execute("delete from tasks where kind = %s", [kind])


async def _connect(url: str) -> Conn:
    return await psycopg.AsyncConnection.connect(url, autocommit=True)


async def test_a_locked_row_is_skipped_by_the_other_claimer(
    migrated_database: str, shared_kind: str
) -> None:
    async with await _connect(migrated_database) as setup:
        first = await queue.enqueue(setup, shared_kind, "1")
        second = await queue.enqueue(setup, shared_kind, "2")

    async with (
        await psycopg.AsyncConnection.connect(migrated_database) as worker_a,
        await _connect(migrated_database) as worker_b,
    ):
        held = await queue.claim(worker_a, [shared_kind], LOCK)  # not committed yet
        other = await queue.claim(worker_b, [shared_kind], LOCK)
        await worker_a.commit()
        after_commit = await queue.claim(worker_b, [shared_kind], LOCK)

    assert held is not None and held.task_id == first.task_id
    assert other is not None and other.task_id == second.task_id
    assert after_commit is None  # the first task is running under worker A's lock


async def test_two_claimers_never_process_a_task_twice(
    migrated_database: str, shared_kind: str
) -> None:
    async with await _connect(migrated_database) as setup:
        created = [await queue.enqueue(setup, shared_kind, str(n)) for n in range(200)]
    processed: list[list[int]] = [[], []]

    async def worker(index: int) -> None:
        async with await _connect(migrated_database) as conn:
            while (task := await queue.claim(conn, [shared_kind], LOCK)) is not None:
                processed[index].append(task.task_id)
                await asyncio.sleep(0)  # let the other worker in
                assert await queue.complete(conn, task) is not None

    await asyncio.gather(worker(0), worker(1))

    every = processed[0] + processed[1]
    assert sorted(every) == sorted(task.task_id for task in created)  # each exactly once
    assert processed[0] and processed[1]  # both workers took part
