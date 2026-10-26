"""E7a: fiscal labels for a quarter that ended on a date (D-008, D-074), checked on the year
ends of universe companies: 52/53-week years, January retailers, J&J's early-January year end
and Costco's 12- and 16-week quarters."""

from datetime import date

import pytest

from marketdata.normalize.periods import FiscalPeriod, fiscal_quarter


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
