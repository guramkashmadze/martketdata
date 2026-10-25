"""Task kind to async handler (E3-S2).

A handler is `async def handler(context, task) -> None`. It returns when the task is done and
raises when it isn't: `TaskRefused` when retrying can't help (the task becomes dead at once),
anything else to have the task retried after its kind's backoff. A task can run again after a
worker dies half-way, so handlers write only idempotent upserts (PLAN.md §6).

New epics add a handler here and nothing else; the watcher routes work only to registered kinds
(PLAN.md §4).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from marketdata.config import Settings
from marketdata.db.repositories.tasks import Task
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.tasks.queue import DEFAULT_RETRY, RetryPolicy


class TaskRefused(Exception):
    """Retrying can't help: bad input, or a load the safety limit refused. The task is dead at
    once and the worker logs it as an error for the operator."""


@dataclass(frozen=True)
class TaskContext:
    """What handlers get: settings, database connections and the SEC, raw store first."""

    settings: Settings
    connect: Connect  # a connection whose transaction commits on exit
    fetcher: RawFetcher
    kinds: frozenset[str]  # the registered kinds: work is routed only to these (§4)


Handler = Callable[[TaskContext, Task], Awaitable[None]]


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    handler: Handler
    retry: RetryPolicy


class Registry:
    def __init__(self) -> None:
        self._specs: dict[str, TaskSpec] = {}

    def register(self, kind: str, handler: Handler, *, retry: RetryPolicy = DEFAULT_RETRY) -> None:
        if kind in self._specs:
            raise ValueError(f"task kind {kind!r} is already registered")
        self._specs[kind] = TaskSpec(kind, handler, retry)

    def spec(self, kind: str) -> TaskSpec:
        return self._specs[kind]

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(self._specs)

    def __contains__(self, kind: object) -> bool:
        return kind in self._specs
