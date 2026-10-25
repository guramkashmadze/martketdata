"""E3-S2: the worker claims tasks, runs their handlers and records the outcome.

The worker uses its own pool, so the tasks are committed; each test has a kind of its own and
deletes its tasks afterwards. Logs are read as JSON from stderr.
"""

import asyncio
import json
import logging
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import structlog
from conftest import sec_serving

from marketdata.config import Settings
from marketdata.db.pool import Pool, create_pool
from marketdata.db.repositories.tasks import Task
from marketdata.ingest.fetch import RawFetcher
from marketdata.log import configure_logging
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.registry import Handler, Registry, TaskContext, TaskRefused
from marketdata.tasks.worker import Worker, run_until_stopped

FAST = {"poll_interval": 0.02}


@dataclass
class Env:
    url: str
    kind: str
    pool: Pool
    context: Callable[[Registry], TaskContext]


@pytest.fixture
async def env(migrated_database: str, tmp_path: Path) -> AsyncIterator[Env]:
    kind = f"test-worker-{uuid4().hex[:8]}"
    settings = Settings(_env_file=None, database_url=migrated_database)  # type: ignore[call-arg]
    async with create_pool(migrated_database, max_size=8) as pool, sec_serving({}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), pool.connection)

        def context(registry: Registry) -> TaskContext:
            return TaskContext(settings, pool.connection, fetcher, registry.kinds)

        yield Env(migrated_database, kind, pool, context)
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from tasks where kind = %s", [kind])


class JsonLogs:
    """JSON log lines on stderr. Call start() in the test itself: capsys gives each test phase
    a new stream, and the log handler keeps the stream it was given."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self._capsys = capsys

    def start(self) -> None:
        configure_logging("live", "DEBUG")

    def read(self) -> list[dict[str, Any]]:
        lines = self._capsys.readouterr().err.splitlines()
        return [json.loads(line) for line in lines if line.startswith("{")]


@pytest.fixture
def json_logs(capsys: pytest.CaptureFixture[str]) -> Iterator[JsonLogs]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield JsonLogs(capsys)
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def _registry(kind: str, handler: Handler, **options: Any) -> Registry:
    registry = Registry()
    registry.register(kind, handler, **options)
    return registry


async def _enqueue(env: Env, *keys: str) -> list[Task]:
    async with env.pool.connection() as conn:
        return [await queue.enqueue(conn, env.kind, key) for key in keys]


async def _tasks(env: Env) -> dict[str, Task]:
    async with env.pool.connection() as conn:
        return {task.key: task for task in await queue.list_tasks(conn, kind=env.kind)}


async def _until(check: Callable[[], Awaitable[bool]], within: float = 10.0) -> None:
    async with asyncio.timeout(within):
        while not await check():
            await asyncio.sleep(0.02)


async def _all(env: Env, *statuses: str) -> bool:
    return all(task.status in statuses for task in (await _tasks(env)).values())


async def test_each_task_runs_once_and_is_marked_done(env: Env) -> None:
    seen: list[str] = []

    async def handler(context: TaskContext, task: Task) -> None:  # noqa: ASYNC124
        seen.append(task.key)

    await _enqueue(env, "1", "2", "3")
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), **FAST)
    running = asyncio.create_task(worker.run())

    await _until(lambda: _all(env, "done"))
    worker.stop()
    await asyncio.wait_for(running, 10)

    assert sorted(seen) == ["1", "2", "3"]
    assert {task.attempts for task in (await _tasks(env)).values()} == {1}


async def test_no_more_than_concurrency_attempts_run_at_once(env: Env) -> None:
    active = peak = 0

    async def handler(context: TaskContext, task: Task) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    await _enqueue(env, *[str(n) for n in range(6)])
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), concurrency=2, **FAST)
    running = asyncio.create_task(worker.run())

    await _until(lambda: _all(env, "done"))
    worker.stop()
    await asyncio.wait_for(running, 10)

    assert peak == 2


async def test_a_failing_handler_is_logged_with_its_task_and_retried_later(
    env: Env, json_logs: JsonLogs
) -> None:
    async def handler(context: TaskContext, task: Task) -> None:  # noqa: ASYNC124
        raise RuntimeError("boom")

    json_logs.start()
    [created] = await _enqueue(env, "320193")
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), **FAST)
    running = asyncio.create_task(worker.run())

    await _until(lambda: _all(env, "failed"))
    worker.stop()
    await asyncio.wait_for(running, 10)

    failed = (await _tasks(env))["320193"]
    assert (failed.attempts, failed.last_error) == (1, "RuntimeError: boom")
    assert failed.run_after - failed.updated_at == timedelta(minutes=1)
    [entry] = [line for line in json_logs.read() if line["event"] == "task_failed"]
    assert entry["level"] == "warning"
    assert (entry["task_id"], entry["kind"], entry["key"], entry["attempt"]) == (
        created.task_id,
        env.kind,
        "320193",
        1,
    )
    assert entry["error"] == "RuntimeError: boom"
    assert entry["retry_at"] == failed.run_after.isoformat()
    assert "RuntimeError" in json.dumps(entry["exception"])  # the traceback


async def test_a_refused_task_is_dead_at_once_and_logged_as_an_error(
    env: Env, json_logs: JsonLogs
) -> None:
    async def handler(context: TaskContext, task: Task) -> None:  # noqa: ASYNC124
        raise TaskRefused("the ticker lists look cut short")

    json_logs.start()
    await _enqueue(env, "2026-10-25")
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), **FAST)
    running = asyncio.create_task(worker.run())

    await _until(lambda: _all(env, "dead"))
    worker.stop()
    await asyncio.wait_for(running, 10)

    dead = (await _tasks(env))["2026-10-25"]
    assert (dead.attempts, dead.last_error) == (1, "TaskRefused: the ticker lists look cut short")
    [entry] = [line for line in json_logs.read() if line["event"] == "task_dead"]
    assert (entry["level"], entry["refused"], entry["key"]) == ("error", True, "2026-10-25")


async def test_a_task_whose_worker_died_in_its_last_attempt_is_not_run_again(env: Env) -> None:
    calls = 0

    async def handler(context: TaskContext, task: Task) -> None:  # noqa: ASYNC124
        nonlocal calls
        calls += 1

    [created] = await _enqueue(env, "1")
    async with env.pool.connection() as conn:  # a worker died during attempt 5 of 5
        await conn.execute(
            "update tasks set status = 'running', attempts = 5, "
            "locked_until = now() - interval '1 minute' where task_id = %s",
            [created.task_id],
        )
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), **FAST)
    running = asyncio.create_task(worker.run())

    await _until(lambda: _all(env, "dead"))
    worker.stop()
    await asyncio.wait_for(running, 10)

    dead = (await _tasks(env))["1"]
    assert calls == 0
    assert dead.last_error == ("TaskRefused: its worker stopped during attempt 5, the last allowed")


async def test_the_lock_of_a_long_task_is_renewed(env: Env) -> None:
    seen: list[Any] = []

    async def handler(context: TaskContext, task: Task) -> None:
        seen.append((await _tasks(env))[task.key].locked_until)
        await asyncio.sleep(0.5)
        seen.append((await _tasks(env))[task.key].locked_until)

    await _enqueue(env, "1")
    worker = Worker(
        _registry(env.kind, handler),
        env.context(Registry()),
        lock_for=timedelta(seconds=30),
        heartbeat_every=0.1,
        **FAST,
    )
    running = asyncio.create_task(worker.run())

    await _until(lambda: _all(env, "done"))
    worker.stop()
    await asyncio.wait_for(running, 10)

    assert seen[1] > seen[0]


async def test_a_stop_lets_running_tasks_finish_and_claims_nothing_new(env: Env) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def handler(context: TaskContext, task: Task) -> None:
        started.set()
        await finish.wait()

    await _enqueue(env, "1", "2")
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), concurrency=1, **FAST)
    running = asyncio.create_task(run_until_stopped(worker))
    await asyncio.wait_for(started.wait(), 10)

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0.2)
    assert not running.done()  # waiting for the running task
    finish.set()
    await asyncio.wait_for(running, 10)

    statuses = {key: task.status for key, task in (await _tasks(env)).items()}
    assert statuses == {"1": "done", "2": "queued"}


async def test_tasks_still_running_after_the_grace_period_go_back_to_the_queue(env: Env) -> None:
    started = asyncio.Event()

    async def handler(context: TaskContext, task: Task) -> None:
        started.set()
        await asyncio.Event().wait()  # never finishes

    await _enqueue(env, "1")
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), grace=0.2, **FAST)
    running = asyncio.create_task(worker.run())
    await asyncio.wait_for(started.wait(), 10)

    worker.stop()
    await asyncio.wait_for(running, 10)

    released = (await _tasks(env))["1"]
    assert (released.status, released.attempts, released.locked_until) == ("queued", 0, None)


async def test_a_second_stop_puts_running_tasks_back_at_once(env: Env) -> None:
    started = asyncio.Event()

    async def handler(context: TaskContext, task: Task) -> None:
        started.set()
        await asyncio.Event().wait()

    await _enqueue(env, "1")
    worker = Worker(_registry(env.kind, handler), env.context(Registry()), grace=60, **FAST)
    running = asyncio.create_task(worker.run())
    await asyncio.wait_for(started.wait(), 10)

    worker.stop()
    worker.stop()
    await asyncio.wait_for(running, 5)

    assert (await _tasks(env))["1"].status == "queued"
