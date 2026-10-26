"""E7a-S3: the period of each column, and the current and prior-year quarters (PLAN.md §9.9,
D-073). The headings are spelled as the backfill's releases spell them."""

from datetime import date

import pytest

from marketdata.extract.columns import table_periods
from marketdata.extract.html_tables import Table, parse_html


def _table(headings: list[list[str]], label_rows: tuple[str, ...] = ()) -> Table:
    rows = "".join(f"<tr><td>{label}</td></tr>" for label in label_rows)
    for cells in headings:
        rows += "<tr><td></td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    width = len(headings[-1])
    rows += "<tr><td>Revenue</td>" + "".join(f"<td>{n}</td>" for n in range(1, width + 1)) + "</tr>"
    [table] = parse_html(f"<html><body><table>{rows}</table></body></html>".encode()).tables
    return table


@pytest.mark.parametrize(
    ("heading", "duration"),
    [
        ("Three Months Ended June 27, 2026", "quarter"),
        ("Quarter Ended June 30, 2026", "quarter"),
        ("12 Weeks Ended November 23, 2025", "quarter"),  # Costco
        ("16 Weeks Ended August 30, 2026", "quarter"),
        ("Fourteen Weeks Ended February 3, 2024", "quarter"),  # a 53-week year's Q4
        ("Q3 FY26", "quarter"),
        ("3Q26", "quarter"),
        ("4th Qtr. September 3, 2026", "quarter"),  # Micron
        ("FQ4-26", "quarter"),
        ("Third Quarter 2026", "quarter"),
        ("Nine Months Ended June 27, 2026", "longer"),
        ("Six Months Ended July 31, 2026", "longer"),
        ("Twelve Months Ended December 31, 2025", "longer"),
        ("24 Weeks Ended February 15, 2026", "longer"),
        ("52 Weeks Ended August 30, 2026", "longer"),
        ("Year Ended December 31, 2025", "longer"),
        ("FY26", "longer"),
        ("June 27, 2026", "unknown"),
    ],
)
def test_durations(heading: str, duration: str) -> None:
    periods = table_periods(_table([[heading]]))

    assert periods.columns[0].duration == duration


def test_the_latest_quarter_and_the_one_a_year_before_it() -> None:
    table = _table(
        [
            ["Three Months Ended"] * 3 + ["Nine Months Ended"] * 2,
            ["June 27, 2026", "March 28, 2026", "June 28, 2025", "June 27, 2026", "June 28, 2025"],
        ]
    )

    periods = table_periods(table)

    assert periods.current is not None and periods.prior is not None
    assert (periods.current.index, periods.current.end) == (0, date(2026, 6, 27))
    assert (periods.prior.index, periods.prior.end) == (2, date(2025, 6, 28))  # 364 days


def test_costcos_sixteen_week_fourth_quarter() -> None:
    table = _table(
        [
            ["16 Weeks Ended"] * 2 + ["52 Weeks Ended"] * 2,
            ["August 30, 2026", "August 31, 2025", "August 30, 2026", "August 31, 2025"],
        ]
    )

    periods = table_periods(table)

    assert periods.current is not None and periods.prior is not None
    assert (periods.current.index, periods.prior.index) == (0, 1)


def test_change_and_ratio_columns_hold_no_period() -> None:
    table = _table(
        [
            ["THIRD QUARTER"] * 5,
            ["2026", "2026", "2025", "2025", "Percent Increase (Decrease)"],
            ["Amount", "Percent to Sales", "Amount", "Percent to Sales", ""],
        ]
    )  # J&J

    periods = table_periods(table)

    assert [column.change for column in periods.columns] == [False, True, False, True, True]
    assert periods.current is not None and periods.prior is not None
    assert (periods.current.index, periods.prior.index) == (0, 2)


def test_quarter_labels_without_dates() -> None:
    periods = table_periods(_table([["Q3 FY26", "Q2 FY26", "Q3 FY25", "Q/Q", "Y/Y"]]))

    assert periods.current is not None and periods.prior is not None
    assert (periods.current.heading, periods.prior.heading) == ("Q3 FY26", "Q3 FY25")
    assert periods.current.end is None


def test_a_period_in_the_label_column_applies_to_every_column() -> None:
    table = _table([["2025", "2026"]], label_rows=("Three Months Ended March 31,",))  # Amazon

    periods = table_periods(table)

    assert periods.current is not None and periods.prior is not None
    assert (periods.current.end, periods.prior.end) == (date(2026, 3, 31), date(2025, 3, 31))


def test_a_title_with_the_full_dates_gives_each_column_its_own() -> None:
    title = "For the Three and Nine Months Ended August 2, 2026 and August 3, 2025"  # Deere
    periods = table_periods(_table([["Three Months Ended"] * 2, ["2026", "2025"]]), [title])

    assert periods.current is not None and periods.prior is not None
    assert (periods.current.end, periods.prior.end) == (date(2026, 8, 2), date(2025, 8, 3))


def test_a_sentence_about_fiscal_years_gives_no_dates() -> None:
    sentence = (
        'The fiscal year ending January 31, 2027 is referred to as "2026" and the fiscal year '
        'ended February 1, 2026 is referred to as "2025".'
    )  # lululemon
    periods = table_periods(_table([["Second Quarter 2026", "Second Quarter 2025"]]), [sentence])

    assert periods.current is not None and periods.prior is not None
    assert (periods.current.end, periods.prior.end) == (None, None)


def test_two_columns_for_the_current_quarter_are_ambiguous() -> None:
    periods = table_periods(
        _table([["Three Months Ended"] * 2, ["June 30, 2026", "June 30, 2026"]])
    )

    assert periods.current is None
    assert periods.problem == "2 quarter columns end on 2026-06-30"


def test_a_statement_of_full_years_has_no_quarter() -> None:
    periods = table_periods(_table([["Year Ended December 31,"] * 2, ["2025", "2024"]]))

    assert periods.current is None
    assert periods.problem is not None and "no column is a quarter" in periods.problem


def test_three_and_nine_months_in_a_title_names_both() -> None:
    title = "For the Three and Nine Months Ended August 2, 2026 and August 3, 2025"
    periods = table_periods(_table([["2026", "2025"]]), [title])

    assert [column.duration for column in periods.columns] == ["unknown", "unknown"]
