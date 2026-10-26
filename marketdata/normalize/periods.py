"""Period logic from PLAN.md §9.4 (E4a); fiscal labels for earnings releases (E7a, §9.9).

Pure. Fiscal labels (D-008, D-074):

- `fiscal_year` is the calendar year in which the fiscal year ends, except that a fiscal year
  ending January 1 to 7 takes the year before: a 52/53-week year that follows the calendar
  (J&J's fiscal 2026 ends on 2027-01-03) is that calendar year.
- Quarters are numbered by position: the months from the previous fiscal year end to the
  quarter's end, divided by three and rounded (Costco's 12-, 24-, 36- and 52-week marks fall
  on quarters 1 to 4 too).

`companies.fiscal_year_end` ('MMDD') is the current fiscal year's nominal end; a 52/53-week
year ends within a week of it, so a quarter ending up to 10 days after the nominal date still
closes that fiscal year.
"""

from dataclasses import dataclass
from datetime import date, timedelta

YEAR_END_SLACK = timedelta(days=10)
EARLY_JANUARY = 7  # a fiscal year ending on January 1 to 7 is the calendar year before


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    fiscal_period: str  # Q1..Q4


def fiscal_quarter(period_end: date, fiscal_year_end: str | None) -> FiscalPeriod | None:
    """The fiscal year and quarter of a quarter that ended on `period_end`; None without a
    usable fiscal year end ('MMDD')."""
    month_day = _month_day(fiscal_year_end)
    if month_day is None:
        return None
    month, day = month_day
    year_end = _year_end(period_end.year, month, day)
    if year_end < period_end - YEAR_END_SLACK:
        year_end = _year_end(period_end.year + 1, month, day)
    previous = _year_end(year_end.year - 1, month, day)
    months = (period_end - previous).days / (365.25 / 12)
    quarter = min(4, max(1, round(months / 3)))
    label_year = year_end.year
    if month == 1 and day <= EARLY_JANUARY:
        label_year -= 1
    return FiscalPeriod(label_year, f"Q{quarter}")


def _year_end(year: int, month: int, day: int) -> date:
    """The nominal fiscal year end in `year`; February 29 falls back to the 28th."""
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def _month_day(value: str | None) -> tuple[int, int] | None:
    if value is None or len(value) != 4 or not value.isdigit():
        return None
    month, day = int(value[:2]), int(value[2:])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day
