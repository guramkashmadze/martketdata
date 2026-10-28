"""Period logic from PLAN.md §9.4 (E4a); fiscal labels for earnings releases (E7a, §9.9).

Pure.

Durations (rule 1, widened for Costco in D-083): `period_end - period_start + 1` days. A quarter
is 80 to 120 days (Costco's 12-week quarters are 84 days, its 16- and 17-week fourth quarters
112 and 119), six months 165 to 200 (Costco: 168), nine months 250 to 290 (252) and a year
350 to 380 (52 and 53 weeks). Anything else is no period of ours.

Latest filing wins (rule 2): for each concept, unit and period, the fact filed last, ties to the
latest accession number. Metrics read facts of the 10-K and 10-Q family only (D-083).

Fiscal labels (rule 7; D-008, D-074, D-083):

- `fiscal_year` is the calendar year in which the fiscal year ends, except that a fiscal year
  ending January 1 to 7 takes the year before: a 52/53-week year that follows the calendar
  (J&J's fiscal 2026 ends on 2027-01-03) is that calendar year.
- Quarters are numbered by position: the months from the previous fiscal year end to the
  quarter's end, divided by three and rounded (Costco's 12-, 24-, 36- and 52-week marks fall
  on quarters 1 to 4 too).
- Over history the year ends come from the company's own filings (FiscalCalendar): the end of
  every annual period its 10-Ks report, and the end of each transition report (Deckers' 10-QT
  for January to March 2014, when its year end moved from December to March). After the last
  of those, `companies.fiscal_year_end` ('MMDD', the current fiscal year's nominal end) takes
  over: FuboTV's last 10-K covers 2024, and its 10-Qs since follow a September year.

A 52/53-week year ends within a week of its nominal date, so a quarter ending up to 10 days
after the nominal date still closes that fiscal year.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from marketdata.normalize.companyfacts import XbrlFact

YEAR_END_SLACK = timedelta(days=10)
EARLY_JANUARY = 7  # a fiscal year ending on January 1 to 7 is the calendar year before

type Duration = Literal["Q", "6M", "9M", "FY"]
DURATIONS: tuple[tuple[Duration, int, int], ...] = (
    ("Q", 80, 120),
    ("6M", 165, 200),
    ("9M", 250, 290),
    ("FY", 350, 380),
)

ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
TRANSITION_FORMS = frozenset({"10-KT", "10-KT/A", "10-QT", "10-QT/A"})
# Facts that feed metrics: the 10-K and 10-Q family, transition reports included (D-083).
METRIC_FORMS = ANNUAL_FORMS | TRANSITION_FORMS | {"10-Q", "10-Q/A"}

type PeriodKey = tuple[str, str, str, date | None, date]


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    fiscal_period: str  # Q1..Q4


def duration_days(start: date, end: date) -> int:
    return (end - start).days + 1


def classify(start: date | None, end: date) -> Duration | None:
    """Rule 1: the kind of period from `start` to `end`; None for an instant or a length that
    is no period of ours."""
    if start is None:
        return None
    days = duration_days(start, end)
    for duration, low, high in DURATIONS:
        if low <= days <= high:
            return duration
    return None


def period_key(fact: XbrlFact) -> PeriodKey:
    return (fact.taxonomy, fact.concept, fact.unit, fact.period_start, fact.period_end)


def latest_facts(facts: Iterable[XbrlFact]) -> dict[PeriodKey, XbrlFact]:
    """Rule 2: for each concept, unit and period, the fact filed last; ties go to the latest
    accession number. Every fact stays stored; this only chooses."""
    chosen: dict[PeriodKey, XbrlFact] = {}
    for fact in facts:
        key = period_key(fact)
        kept = chosen.get(key)
        if kept is None or (fact.filed_at, fact.accession_no) > (kept.filed_at, kept.accession_no):
            chosen[key] = fact
    return chosen


@dataclass(frozen=True)
class FiscalCalendar:
    """A company's fiscal year ends: the ones its filings report, in order, then the current
    year's nominal end."""

    year_ends: tuple[date, ...]
    fiscal_year_end: str | None  # 'MMDD' from submissions

    @classmethod
    def from_facts(cls, facts: Iterable[XbrlFact], fiscal_year_end: str | None) -> "FiscalCalendar":
        ends: set[date] = set()
        transition: dict[str, date] = {}
        for fact in facts:
            if fact.period_start is None:
                continue
            if fact.form in ANNUAL_FORMS and classify(fact.period_start, fact.period_end) == "FY":
                ends.add(fact.period_end)
            elif fact.form in TRANSITION_FORMS:
                last = transition.get(fact.accession_no)
                if last is None or fact.period_end > last:
                    transition[fact.accession_no] = fact.period_end
        return cls(tuple(sorted(ends | set(transition.values()))), fiscal_year_end)

    def is_year_end(self, day: date) -> bool:
        return day in self.year_ends

    def year(self, year_end: date) -> int:
        """The fiscal year a year ending on `year_end` is labelled (D-008, D-074)."""
        return _label_year(year_end)

    def quarter(self, period_end: date) -> FiscalPeriod | None:
        """The fiscal year and quarter of a quarter that ended on `period_end`; None when no
        year end is known and the nominal one isn't usable."""
        known = next((end for end in self.year_ends if end >= period_end), None)
        if known is not None:
            earlier = [end for end in self.year_ends if end < period_end]
            previous = earlier[-1] if earlier else _year_end(known.year - 1, known.month, known.day)
            return FiscalPeriod(_label_year(known), f"Q{_position(previous, period_end)}")
        month_day = _month_day(self.fiscal_year_end)
        if month_day is None:
            if not self.year_ends:
                return None
            month_day = (self.year_ends[-1].month, self.year_ends[-1].day)
        month, day = month_day
        year_end = _year_end(period_end.year, month, day)
        if year_end < period_end - YEAR_END_SLACK:
            year_end = _year_end(period_end.year + 1, month, day)
        previous = _year_end(year_end.year - 1, month, day)
        if self.year_ends and self.year_ends[-1] > previous:
            previous = self.year_ends[-1]  # a changed year end: count from the last real one
        return FiscalPeriod(_label_year(year_end), f"Q{_position(previous, period_end)}")


def fiscal_quarter(period_end: date, fiscal_year_end: str | None) -> FiscalPeriod | None:
    """The fiscal year and quarter of a quarter that ended on `period_end`, from the nominal
    year end alone ('MMDD'); None without a usable one. E7a labels releases with it."""
    if _month_day(fiscal_year_end) is None:
        return None
    return FiscalCalendar((), fiscal_year_end).quarter(period_end)


def _position(previous: date, period_end: date) -> int:
    months = (period_end - previous).days / (365.25 / 12)
    return min(4, max(1, round(months / 3)))


def _label_year(year_end: date) -> int:
    if year_end.month == 1 and year_end.day <= EARLY_JANUARY:
        return year_end.year - 1
    return year_end.year


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
