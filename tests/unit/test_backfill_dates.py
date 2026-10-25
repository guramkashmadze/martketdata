"""E3-S5: the backfill window and the task payload it travels in."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from marketdata.db.repositories.tasks import Task
from marketdata.tasks.handlers import backfill_filings
from marketdata.tasks.registry import TaskRefused
from marketdata.tasks.scheduler import years_before


def test_years_before() -> None:
    assert years_before(date(2026, 10, 26), 5) == date(2021, 10, 26)
    assert years_before(date(2024, 2, 29), 1) == date(2023, 2, 28)


def _task(payload: dict[str, Any]) -> Task:
    moment = datetime(2026, 10, 26, tzinfo=UTC)
    return Task(
        task_id=1,
        kind="filings_backfill",
        key="320193",
        payload=payload,
        status="running",
        attempts=1,
        run_after=moment,
        locked_until=moment,
        last_error=None,
        created_at=moment,
        updated_at=moment,
    )


@pytest.mark.parametrize(
    "payload",
    [{"cik": 320193}, {"cik": 320193, "since": "26 Oct 2021"}, {"since": "2021-10-26"}],
    ids=["no since", "bad since", "no cik"],
)
async def test_a_payload_without_a_cik_or_start_date_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(TaskRefused, match="payload has no"):
        await backfill_filings(None, _task(payload))  # type: ignore[arg-type]
