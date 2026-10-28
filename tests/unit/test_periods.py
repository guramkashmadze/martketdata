"""E7a: fiscal labels for a quarter that ended on a date (D-008, D-074), checked on the year
ends of universe companies: 52/53-week years, January retailers, J&J's early-January year end
and Costco's 12- and 16-week quarters. E4a-S3: §9.4 rules 1, 2 and 7 over a company's history:
durations, the latest filing, and year ends from its own 10-Ks (D-083)."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.companyfacts import XbrlFact, parse_companyfacts
from marketdata.normalize.periods import (
    FiscalCalendar,
    FiscalPeriod,
    classify,
    fiscal_quarter,
    latest_facts,
)


@pytest.mark.parametrize(
    ("period_end", "fiscal_year_end", "year", "quarter"),
    [
        (date(2026, 6, 27), "0926", 2026, "Q3"),  # Apple
        (date(2025, 9, 27), "0926", 2025, "Q4"),  # ends a day after the nominal year end
        (date(2025, 12, 27), "0926", 2026, "Q1"),
        (date(2026, 8, 1), "0201", 2027, "Q2"),  # Target names this fiscal 2026 (D-008)
        (date(2026, 1, 31), "0201", 2026, "Q4"),
        (date(2026, 9, 27), "0103", 2026, "Q3"),  # J&J: fiscal 2026 ends 2027-01-03 (D-074)
        (date(2025, 12, 28), "0103", 2025, "Q4"),
        (date(2026, 3, 29), "0103", 2026, "Q1"),
        (date(2025, 11, 23), "0830", 2026, "Q1"),  # Costco: 12 weeks
        (date(2026, 2, 15), "0830", 2026, "Q2"),  # 24 weeks
        (date(2026, 5, 10), "0830", 2026, "Q3"),  # 36 weeks
        (date(2026, 8, 30), "0830", 2026, "Q4"),  # 16 weeks
        (date(2026, 9, 30), "0630", 2027, "Q1"),  # Microsoft
        (date(2025, 10, 26), "0131", 2026, "Q3"),  # NVIDIA
        (date(2026, 9, 3), "0902", 2026, "Q4"),  # Micron's 14-week quarter
        (date(2026, 9, 30), "1231", 2026, "Q3"),
    ],
)
def test_fiscal_quarter(period_end: date, fiscal_year_end: str, year: int, quarter: str) -> None:
    assert fiscal_quarter(period_end, fiscal_year_end) == FiscalPeriod(year, quarter)


@pytest.mark.parametrize("fiscal_year_end", [None, "", "13XX", "1340", "926"])
def test_no_usable_fiscal_year_end_gives_no_label(fiscal_year_end: str | None) -> None:
    assert fiscal_quarter(date(2026, 6, 30), fiscal_year_end) is None


# --- E4a-S3: durations, the latest filing, and fiscal labels over a company's history.

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts"
FILED = date(2026, 3, 1)


def _facts(name: str) -> tuple[XbrlFact, ...]:
    return parse_companyfacts((FIXTURES / name).read_bytes()).facts


def _fact(
    start: date | None,
    end: date,
    *,
    form: str = "10-K",
    accession: str = "0000000001-26-000001",
    filed: date = FILED,
    value: int = 1,
) -> XbrlFact:
    return XbrlFact(
        cik=1,
        taxonomy="us-gaap",
        concept="Revenues",
        unit="USD",
        period_start=start,
        period_end=end,
        value=Decimal(value),
        fy=None,
        fp=None,
        form=form,
        frame=None,
        filed_at=filed,
        accession_no=accession,
    )


@pytest.mark.parametrize(
    ("days", "duration"),
    [
        (79, None),
        (84, "Q"),  # Costco's 12 weeks
        (91, "Q"),
        (98, "Q"),  # Apple's and Micron's 14 weeks
        (112, "Q"),  # Costco's 16-week fourth quarter
        (119, "Q"),  # and its 17 weeks in a 53-week year
        (121, None),
        (168, "6M"),  # Costco's 24 weeks
        (182, "6M"),
        (252, "9M"),  # Costco's 36 weeks
        (273, "9M"),
        (349, None),
        (364, "FY"),
        (371, "FY"),  # 53 weeks
        (381, None),
    ],
)
def test_durations(days: int, duration: str | None) -> None:
    start = date(2025, 9, 1)
    assert classify(start, start + timedelta(days=days - 1)) == duration


def test_an_instant_has_no_duration() -> None:
    assert classify(None, date(2026, 6, 30)) is None


def test_the_latest_filing_wins_and_a_tie_goes_to_the_latest_accession() -> None:
    period = (date(2025, 1, 1), date(2025, 3, 31))
    first = _fact(*period, form="10-Q", accession="0000000001-25-000010", filed=date(2025, 5, 1))
    restated = _fact(*period, form="10-Q", accession="0000000001-26-000010", filed=date(2026, 5, 1))
    tie = _fact(*period, form="10-Q/A", accession="0000000001-26-000011", filed=date(2026, 5, 1))
    other = _fact(None, date(2025, 3, 31), form="10-Q")

    chosen = latest_facts([tie, first, restated, other])

    assert [fact.accession_no for fact in chosen.values()] == [tie.accession_no, other.accession_no]


@pytest.mark.parametrize(
    ("name", "period_end", "year", "quarter"),
    [
        # Apple, September: 52/53 weeks; fiscal 2023 had 53, its first quarter 14 weeks.
        ("AAPL.json", date(2022, 12, 31), 2023, "Q1"),
        ("AAPL.json", date(2023, 9, 30), 2023, "Q4"),
        ("AAPL.json", date(2025, 6, 28), 2025, "Q3"),
        ("AAPL.json", date(2025, 12, 27), 2026, "Q1"),  # after the last 10-K: nominal 0926
        ("AAPL.json", date(2026, 6, 27), 2026, "Q3"),
        # Target, January: 52/53 weeks, the year to 2024-02-03 had 53.
        ("TGT.json", date(2024, 2, 3), 2024, "Q4"),
        ("TGT.json", date(2025, 11, 1), 2026, "Q3"),
        ("TGT.json", date(2026, 8, 1), 2027, "Q2"),  # Target names this fiscal 2026 (D-008)
        # Alphabet, December.
        ("GOOGL.json", date(2025, 9, 30), 2025, "Q3"),
        ("GOOGL.json", date(2025, 12, 31), 2025, "Q4"),
        ("GOOGL.json", date(2026, 6, 30), 2026, "Q2"),
    ],
)
def test_fiscal_labels_from_the_companys_own_year_ends(
    name: str, period_end: date, year: int, quarter: str
) -> None:
    nominal = {"AAPL.json": "0926", "TGT.json": "0201", "GOOGL.json": "1231"}[name]
    calendar = FiscalCalendar.from_facts(_facts(name), nominal)

    assert calendar.quarter(period_end) == FiscalPeriod(year, quarter)


def test_year_ends_are_the_annual_periods_of_10ks() -> None:
    calendar = FiscalCalendar.from_facts(_facts("TGT.json"), "0201")

    assert calendar.year_ends[-3:] == (date(2024, 2, 3), date(2025, 2, 1), date(2026, 1, 31))
    assert calendar.is_year_end(date(2025, 2, 1)) and not calendar.is_year_end(date(2025, 2, 2))
    assert date(2019, 11, 2) not in calendar.year_ends  # a quarter a 10-K reports


def test_only_10ks_and_transition_reports_give_year_ends() -> None:
    twelve_months = (date(2025, 7, 1), date(2026, 6, 30))
    facts = [
        _fact(*twelve_months, form="10-Q"),  # a trailing twelve months
        _fact(*twelve_months, form="8-K"),
        _fact(date(2025, 1, 1), date(2025, 12, 31), form="10-K"),
    ]

    assert FiscalCalendar.from_facts(facts, "1231").year_ends == (date(2025, 12, 31),)


def test_an_early_january_year_end_is_the_year_before() -> None:
    # J&J's 52/53-week year follows the calendar: fiscal 2026 ends on 2027-01-03 (D-074).
    facts = [
        _fact(date(2023, 12, 31), date(2024, 12, 29)),
        _fact(date(2024, 12, 30), date(2025, 12, 28)),
    ]
    calendar = FiscalCalendar.from_facts(facts, "0103")

    assert calendar.quarter(date(2025, 12, 28)) == FiscalPeriod(2025, "Q4")
    assert calendar.quarter(date(2026, 9, 27)) == FiscalPeriod(2026, "Q3")
    assert calendar.year(date(2027, 1, 3)) == 2026


def test_a_changed_year_end_counts_from_the_last_real_one() -> None:
    # FuboTV: December years to 2024, then a September year (submissions say 0930) after the
    # combination with Hulu + Live TV, with no transition report.
    facts = [
        _fact(date(2023, 1, 1), date(2023, 12, 31)),
        _fact(date(2024, 1, 1), date(2024, 12, 31)),
    ]
    calendar = FiscalCalendar.from_facts(facts, "0930")

    assert calendar.quarter(date(2024, 9, 30)) == FiscalPeriod(2024, "Q3")
    assert calendar.quarter(date(2025, 3, 31)) == FiscalPeriod(2025, "Q1")
    assert calendar.quarter(date(2025, 9, 30)) == FiscalPeriod(2025, "Q3")  # a short year
    assert calendar.quarter(date(2025, 12, 31)) == FiscalPeriod(2026, "Q1")
    assert calendar.quarter(date(2026, 6, 30)) == FiscalPeriod(2026, "Q3")


def test_a_transition_report_ends_a_year() -> None:
    # Deckers moved its year end from December to March with a 10-QT for January-March 2014.
    facts = [
        _fact(date(2013, 1, 1), date(2013, 12, 31)),
        _fact(date(2014, 1, 1), date(2014, 3, 31), form="10-QT", accession="0000910521-14-000013"),
        _fact(date(2014, 4, 1), date(2015, 3, 31)),
    ]
    calendar = FiscalCalendar.from_facts(facts, "0331")

    assert calendar.year_ends == (date(2013, 12, 31), date(2014, 3, 31), date(2015, 3, 31))
    assert calendar.quarter(date(2014, 6, 30)) == FiscalPeriod(2015, "Q1")
    assert calendar.quarter(date(2014, 12, 31)) == FiscalPeriod(2015, "Q3")
    assert calendar.quarter(date(2015, 3, 31)) == FiscalPeriod(2015, "Q4")


def test_costcos_weeks_fall_on_their_quarters() -> None:
    # 12-week quarters, a 17-week fourth quarter in the 53-week year to 2023-09-03.
    facts = [
        _fact(date(2022, 8, 29), date(2023, 9, 3)),
        _fact(date(2023, 9, 4), date(2024, 9, 1)),
    ]
    calendar = FiscalCalendar.from_facts(facts, "0830")

    assert [
        calendar.quarter(day)
        for day in (date(2023, 9, 3), date(2023, 11, 26), date(2024, 2, 18), date(2024, 5, 12))
    ] == [
        FiscalPeriod(2023, "Q4"),
        FiscalPeriod(2024, "Q1"),
        FiscalPeriod(2024, "Q2"),
        FiscalPeriod(2024, "Q3"),
    ]


def test_without_year_ends_or_a_nominal_one_there_is_no_label() -> None:
    assert FiscalCalendar((), None).quarter(date(2026, 6, 30)) is None
