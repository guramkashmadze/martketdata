"""Worker process: claims tasks and runs their handlers (E3-S2, D-007).

One worker process does all queued work with async concurrency, so the SEC rate limit lives in
one place (D-007); it holds the SEC slot while it runs (ingest/sec_lock.py).

- Up to `concurrency` attempts run at once. A free slot claims the oldest ready task of the
  registered kinds; with nothing ready the worker looks again every `poll_interval`.
- A claimed task is locked for `lock_for`. The worker renews the locks of its running tasks
  every `heartbeat_every`, so a long task keeps its lock, and the tasks of a worker that died
  are claimed again once their locks run out.
- A handler's exception is logged with the task's id, kind, key and attempt, and stored in
  `last_error`. The task is retried after its kind's backoff, or dead after its last attempt or
  when the handler raised TaskRefused.
- `stop()` (SIGTERM or Ctrl-C) stops claiming and waits up to `grace` for running attempts.
  Attempts still running then are cancelled and their tasks put back in the queue without
  counting the attempt. A second stop cancels them at once.
"""

import asyncio
import signal
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg
import structlog

from marketdata.config import Settings
from marketdata.db.pool import create_pool
from marketdata.db.repositories.tasks import Task
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.ingest.sec_lock import sec_process_lock
from marketdata.sec.client import SecClient
from marketdata.sec.raw_store import RawStore
from marketdata.tasks import queue
from marketdata.tasks.registry import Registry, TaskContext, TaskRefused

LOCK_FOR = timedelta(minutes=5)
HEARTBEAT_EVERY = 60.0  # seconds
POLL_INTERVAL = 1.0  # seconds
GRACE = 60.0  # seconds
MAX_ERROR = 2000  # characters of an error kept in last_error

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Outcome:
    error: Exception | None  # None: the attempt succeeded
    refused: bool
    seconds: float


class Worker:
    def __init__(
        self,
        registry: Registry,
        context: TaskContext,
        *,
        concurrency: int = 4,
        lock_for: timedelta = LOCK_FOR,
        heartbeat_every: float = HEARTBEAT_EVERY,
        poll_interval: float = POLL_INTERVAL,
        grace: float = GRACE,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._registry = registry
        self._context = context
        self._connect: Connect = context.connect
        self._concurrency = concurrency
        self._lock_for = lock_for
        self._heartbeat_every = heartbeat_every
        self._poll_interval = poll_interval
        self._grace = grace
        self._stopping = asyncio.Event()
        self._hurry = asyncio.Event()

    def stop(self) -> None:
        """Stop claiming and finish; called a second time, cancel running attempts now."""
        if self._stopping.is_set():
            self._hurry.set()
        self._stopping.set()

    async def run(self) -> None:
        """Claim and run tasks until stop() is called, then drain."""
        loop = asyncio.get_running_loop()
        held: dict[asyncio.Task[_Outcome], Task] = {}
        stop_requested = asyncio.create_task(self._stopping.wait())
        renew_at = loop.time() + self._heartbeat_every
        log.info(
            "worker_started", kinds=sorted(self._registry.kinds), concurrency=self._concurrency
        )
        try:
            while not self._stopping.is_set():
                while len(held) < self._concurrency and not self._stopping.is_set():
                    task = await self._claim()
                    if task is None:
                        break
                    held[asyncio.create_task(self._attempt(task))] = task
                timeout = max(0.0, min(self._poll_interval, renew_at - loop.time()))
                await asyncio.wait(
                    [stop_requested, *held], timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
                for runner in [runner for runner in held if runner.done()]:
                    await self._record(held.pop(runner), runner.result())
                if loop.time() >= renew_at:
                    await self._renew(list(held.values()))
                    renew_at = loop.time() + self._heartbeat_every
            await self._drain(held)
        finally:
            stop_requested.cancel()
        log.info("worker_stopped")

    async def _drain(self, held: dict[asyncio.Task[_Outcome], Task]) -> None:
        """Wait up to `grace` for running attempts, cancel the rest and put them back."""
        if not held:
            return
        log.info("worker_stopping", running=len(held), grace_seconds=self._grace)
        hurry = asyncio.create_task(self._hurry.wait())
        waiters: list[asyncio.Future[Any]] = [asyncio.gather(*held, return_exceptions=True), hurry]
        await asyncio.wait(waiters, timeout=self._grace, return_when=asyncio.FIRST_COMPLETED)
        hurry.cancel()
        unfinished = [runner for runner in held if not runner.done()]
        for runner in unfinished:
            runner.cancel()
        if unfinished:
            await asyncio.wait(unfinished)
        for runner, task in held.items():
            if runner.cancelled():
                await self._release(task)
            else:
                await self._record(task, runner.result())

    async def _claim(self) -> Task | None:
        try:
            async with self._connect() as conn:
                return await queue.claim(conn, sorted(self._registry.kinds), self._lock_for)
        except psycopg.Error as exc:
            log.error("worker_db_error", step="claim", error=str(exc))
            return None

    async def _attempt(self, task: Task) -> _Outcome:
        spec = self._registry.spec(task.kind)
        with structlog.contextvars.bound_contextvars(
            task_id=task.task_id, kind=task.kind, key=task.key, attempt=task.attempts
        ):
            if task.attempts > spec.retry.max_attempts:
                # Claimed again after its worker died during the last attempt.
                refused = TaskRefused(
                    f"its worker stopped during attempt {task.attempts - 1}, the last allowed"
                )
                return _Outcome(refused, refused=True, seconds=0.0)
            log.info("task_started")
            started = time.monotonic()
            try:
                await spec.handler(self._context, task)
            except TaskRefused as exc:
                return _Outcome(exc, refused=True, seconds=time.monotonic() - started)
            except Exception as exc:
                return _Outcome(exc, refused=False, seconds=time.monotonic() - started)
            return _Outcome(None, refused=False, seconds=time.monotonic() - started)

    async def _record(self, task: Task, outcome: _Outcome) -> None:
        bound = log.bind(task_id=task.task_id, kind=task.kind, key=task.key, attempt=task.attempts)
        spec = self._registry.spec(task.kind)
        try:
            async with self._connect() as conn:
                if outcome.error is None:
                    result = await queue.complete(conn, task)
                else:
                    result = await queue.fail(
                        conn, task, describe(outcome.error), spec.retry, permanent=outcome.refused
                    )
        except psycopg.Error as exc:
            # The task stays running until its lock runs out; then it is claimed again.
            bound.error("worker_db_error", step="record", error=str(exc))
            return
        seconds = round(outcome.seconds, 3)
        if result is None:
            bound.warning("task_lost", reason="its lock ran out and another worker claimed it")
        elif result.status == "done":
            bound.info("task_done", seconds=seconds)
        elif result.status == "dead":
            bound.error(
                "task_dead",
                error=result.last_error,
                refused=outcome.refused,
                seconds=seconds,
                exc_info=None if outcome.refused else outcome.error,
            )
        else:
            bound.warning(
                "task_failed",
                error=result.last_error,
                retry_at=result.run_after.isoformat(),
                seconds=seconds,
                exc_info=outcome.error,
            )

    async def _release(self, task: Task) -> None:
        try:
            async with self._connect() as conn:
                await queue.release(conn, task)
        except psycopg.Error as exc:
            log.error("worker_db_error", step="release", task_id=task.task_id, error=str(exc))
            return
        log.info("task_released", task_id=task.task_id, kind=task.kind, key=task.key)

    async def _renew(self, tasks: list[Task]) -> None:
        if not tasks:
            return
        try:
            async with self._connect() as conn:
                for task in tasks:
                    if not await queue.extend_lock(conn, task, self._lock_for):
                        log.warning("task_lock_lost", task_id=task.task_id, kind=task.kind)
        except psycopg.Error as exc:
            log.error("worker_db_error", step="heartbeat", error=str(exc))


def describe(error: BaseException) -> str:
    """An error as last_error keeps it: its type and message, cut to MAX_ERROR characters."""
    text = f"{type(error).__name__}: {error}"
    return text if len(text) <= MAX_ERROR else text[: MAX_ERROR - 1] + "…"


async def run_until_stopped(worker: Worker) -> None:
    """Run the worker; SIGTERM and SIGINT (Ctrl-C) stop it, a second one hurries it."""
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for signum in signals:
        loop.add_signal_handler(signum, worker.stop)
    try:
        await worker.run()
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)


async def serve(
    settings: Settings,
    registry: Registry,
    sec: SecClient,
    store: RawStore,
    *,
    concurrency: int,
) -> None:
    """The `marketdata worker` process: hold the SEC slot, then run until stopped."""
    database_url = str(settings.database_url)
    async with (
        sec_process_lock(database_url, "marketdata worker"),
        sec,
        # Each attempt may use two connections at once (a transaction and a raw_objects row),
        # plus the worker's own claims and heartbeats.
        create_pool(database_url, max_size=2 * concurrency + 2) as pool,
    ):
        context = TaskContext(
            settings=settings,
            connect=pool.connection,
            fetcher=RawFetcher(sec, store, pool.connection),
            kinds=registry.kinds,
        )
        await run_until_stopped(Worker(registry, context, concurrency=concurrency))
