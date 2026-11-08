"""When the SEC was last checked for the data an answer depends on, and whether that check is
overdue (D-135).

`as_of` says when the newest row an answer uses was written. When the watches stop, it still
looks current: nothing says that two weeks of Forms 4 and 8-Ks may be missing. So every answer
about a company, a comparison or a 13F manager also carries `checked_at`, the time the data it
depends on was last checked against the SEC, and `check_overdue`, true when that was more than a
day ago or never (normalize/freshness.py). Which check applies:

- filings, Forms 4 and earnings releases: the company's own watch, which polls its submissions
  file every 10 minutes while the scheduler and the worker run;
- financials, and the snapshot, whose other parts that watch covers: the watch, unless the
  newest 10-K or 10-Q it found has no facts stored yet; then the time it was found, since the
  financials lack it from then on;
- a comparison: the oldest of its companies' financials checks;
- 13F answers: the managers' own watches: the manager's for its portfolio, the oldest of the
  curated managers' for the holders of a company and for the list of managers.

Decided when read, never stored. It reads the watches' state and the filings table, never the
SEC.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from marketdata.db.repositories import freshness, managers, watch_state
from marketdata.normalize.freshness import (
    REPORT_FORMS,
    check_overdue,
    financials_checked,
    oldest_check,
)

REPORTS_READ = 8  # a company's latest 10-Ks and 10-Qs read for its financials check: two years


@dataclass(frozen=True)
class Check:
    """An answer's check: when the SEC was last checked for its data, and whether that was more
    than a day ago (or never)."""

    at: datetime | None
    overdue: bool


def check_of(at: datetime | None, now: datetime | None = None) -> Check:
    return Check(at=at, overdue=check_overdue(at, now or datetime.now(UTC)))


async def company_check(
    conn: AsyncConnection[Any], cik: int, *, now: datetime | None = None
) -> Check:
    """Filings, Forms 4 and earnings releases: the company's watch."""
    checked = await watch_state.checked_at(conn, [cik])
    return check_of(checked.get(cik), now)


async def financials_check(
    conn: AsyncConnection[Any], cik: int, *, now: datetime | None = None
) -> Check:
    """Financials: the company's watch, or when its newest 10-K or 10-Q without stored facts
    was found."""
    checked = await watch_state.checked_at(conn, [cik])
    reports = await freshness.periodic_reports(conn, cik, REPORT_FORMS, REPORTS_READ)
    return check_of(financials_checked(checked.get(cik), reports), now)


async def comparison_check(
    conn: AsyncConnection[Any], ciks: Sequence[int], *, now: datetime | None = None
) -> Check:
    """Several companies' financials: the oldest of their checks."""
    found = [(await financials_check(conn, cik, now=now)).at for cik in ciks]
    return check_of(oldest_check(found), now)


async def managers_check(
    conn: AsyncConnection[Any],
    manager_ciks: Sequence[int] | None = None,
    *,
    now: datetime | None = None,
) -> Check:
    """13F answers: the oldest watch of these managers, by default every curated one."""
    ciks = list(manager_ciks) if manager_ciks is not None else await managers.curated_ciks(conn)
    checked = await watch_state.checked_at(conn, ciks)
    return check_of(oldest_check(checked.get(cik) for cik in ciks), now)
