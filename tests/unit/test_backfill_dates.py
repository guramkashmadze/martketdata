"""E3-S5, E7a-S5: the backfill windows and the task payloads they travel in."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from marketdata.db.repositories.tasks import Task
from marketdata.tasks.handlers import backfill_filings, extract_earnings
from marketdata.tasks.registry import TaskRefused
from marketdata.tasks.scheduler import months_before, years_before


def test_years_before() -> None:
    assert years_before(date(2026, 10, 26), 5) == date(2021, 10, 26)
    assert years_before(date(2024, 2, 29), 1) == date(2023, 2, 28)


def test_months_before() -> None:
    assert months_before(date(2026, 10, 26), 12) == date(2025, 10, 26)  # four quarters
    assert months_before(date(2026, 1, 15), 1) == date(2025, 12, 15)
    assert months_before(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert months_before(date(2024, 5, 29), 3) == date(2024, 2, 29)


def _task(payload: dict[str, Any]) -> Task:
    moment = datetime(2026, 10, 26, tzinfo=UTC)
    return Task(
        task_id=1,
        kind="filings_backfill",
        key="320193",
        payload=payload,
        status="running",
        priority=-10,
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


@pytest.mark.parametrize(
    "payload",
    [
        {"cik": 320193},
        {"cik": 320193, "accession_no": "0000320193-26"},
        {"accession_no": "0000320193-26-000018"},
    ],
    ids=["no accession", "bad accession", "no cik"],
)
async def test_an_earnings_payload_without_a_cik_or_accession_is_refused(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(TaskRefused, match="payload has no"):
        await extract_earnings(None, _task(payload))  # type: ignore[arg-type]
