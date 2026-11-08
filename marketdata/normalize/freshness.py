"""How fresh an answer is: when the SEC was last checked for the data it depends on (D-135),
and whether a filing a watch finds is still live (D-136).

A watch reads a company's submissions file every 10 minutes while the scheduler and the worker
run (E3). `as_of` says when the newest row an answer uses was written, which looks current even
when the watches have stopped. A day is the line between current and caught up:

- an answer whose data was last checked more than a day ago, or never, says so with
  `check_overdue`: filings newer than that check may be missing from it. Decided when read,
  like `stale_data` (D-090), which says something else: the company's latest 10-Q or 10-K is
  older than 120 days.
- a filing a watch finds more than a day after the SEC accepted it was missed while the
  watches were stopped. It is caught up like a backfill: neither announced nor counted in the
  time to verified (normalize/filings.py, `found_live`).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

CHECK_OVERDUE_AFTER = timedelta(days=1)

# The reports a company's financials check waits for: the originals, as an amendment may bring
# no facts at all (a 10-K/A that adds only Part III).
REPORT_FORMS = frozenset({"10-K", "10-Q", "10-KT", "10-QT"})


def check_overdue(checked_at: datetime | None, now: datetime) -> bool:
    """True when the data was never checked, or last checked more than a day before `now`."""
    return checked_at is None or now - checked_at > CHECK_OVERDUE_AFTER


def oldest_check(checks: Iterable[datetime | None]) -> datetime | None:
    """The check an answer about several companies or managers is as fresh as: the oldest one.
    None when one of them was never checked, or there are none."""
    found = list(checks)
    if not found or any(check is None for check in found):
        return None
    return min(check for check in found if check is not None)


@dataclass(frozen=True)
class PeriodicReport:
    """A 10-K or 10-Q on a company's listing, and whether its XBRL facts are stored."""

    discovered_at: datetime  # when a watch or backfill stored the listing
    loaded: bool  # xbrl_facts holds facts from it


def financials_checked(
    watched: datetime | None, reports: Sequence[PeriodicReport]
) -> datetime | None:
    """When a company's financials were last checked: its watch, while its newest 10-K or 10-Q
    (`reports`, newest first) has facts stored. Otherwise the time the oldest report filed
    after the newest loaded one was found: since then the financials lack what the SEC lists,
    whether the facts refresh is waiting, failed, or the SEC's companyfacts doesn't list the
    filing (KO, V and NEE, E4a). None when the company was never watched."""
    if watched is None:
        return None
    missing = []
    for report in reports:
        if report.loaded:
            break
        missing.append(report.discovered_at)
    return min([watched, *missing])
