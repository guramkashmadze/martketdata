"""E3-S1: how often a task kind is attempted and how long the queue waits between attempts."""

from datetime import timedelta

import pytest

from marketdata.tasks.queue import DEFAULT_RETRY, RetryPolicy

MINUTE = timedelta(minutes=1)


def test_default_is_five_attempts_with_growing_waits() -> None:
    waits = [DEFAULT_RETRY.retry_in(attempts) for attempts in range(1, 6)]

    assert waits == [MINUTE, 5 * MINUTE, 15 * MINUTE, 60 * MINUTE, None]


def test_a_kind_can_set_its_own_limit_and_backoff() -> None:
    # Webhook delivery (PLAN.md §9.11): 3 attempts, about 1 and 10 minutes apart.
    webhook = RetryPolicy(max_attempts=3, backoff=(MINUTE, 10 * MINUTE))

    assert [webhook.retry_in(attempts) for attempts in (1, 2, 3)] == [MINUTE, 10 * MINUTE, None]


def test_the_last_wait_repeats() -> None:
    policy = RetryPolicy(max_attempts=4, backoff=(MINUTE,))

    assert [policy.retry_in(attempts) for attempts in (1, 2, 3, 4)] == [MINUTE] * 3 + [None]


def test_one_attempt_never_retries() -> None:
    assert RetryPolicy(max_attempts=1, backoff=()).retry_in(1) is None


@pytest.mark.parametrize(
    ("max_attempts", "backoff"), [(0, (MINUTE,)), (2, ())], ids=["no attempts", "no backoff"]
)
def test_policies_that_cant_work_are_refused(
    max_attempts: int, backoff: tuple[timedelta, ...]
) -> None:
    with pytest.raises(ValueError, match="must be at least 1|needs at least one backoff"):
        RetryPolicy(max_attempts=max_attempts, backoff=backoff)
