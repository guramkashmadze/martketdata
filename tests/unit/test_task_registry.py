"""E3-S2: the registry maps each task kind to one handler and its retry policy."""

from datetime import timedelta

import pytest

from marketdata.db.repositories.tasks import Task
from marketdata.tasks.queue import DEFAULT_RETRY, RetryPolicy
from marketdata.tasks.registry import Registry, TaskContext


async def handler(context: TaskContext, task: Task) -> None:  # noqa: ASYNC124
    return None


def test_a_kind_gets_its_handler_and_the_default_policy() -> None:
    registry = Registry()
    registry.register("filings_watch", handler)

    spec = registry.spec("filings_watch")

    assert spec.handler is handler
    assert spec.retry == DEFAULT_RETRY
    assert "filings_watch" in registry
    assert registry.kinds == frozenset({"filings_watch"})


def test_a_kind_can_bring_its_own_policy() -> None:
    webhook = RetryPolicy(max_attempts=3, backoff=(timedelta(minutes=1), timedelta(minutes=10)))
    registry = Registry()
    registry.register("webhook_deliver", handler, retry=webhook)

    assert registry.spec("webhook_deliver").retry == webhook


def test_a_kind_is_registered_once() -> None:
    registry = Registry()
    registry.register("filings_watch", handler)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("filings_watch", handler)


def test_unknown_kinds_are_not_in_the_registry() -> None:
    registry = Registry()

    assert "earnings_extract" not in registry
    with pytest.raises(KeyError):
        registry.spec("earnings_extract")
