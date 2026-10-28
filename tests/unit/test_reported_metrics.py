"""E4a-S4: reported metric rows from XBRL facts (§9.4): three-month quarters, fiscal years and
instants at each quarter and year end, each from the first catalog concept with a fact, the
latest filing winning; banks have no revenue. And the reasons a stored value changes."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.changes import StoredMetric, metric_changes
from marketdata.normalize.companyfacts import XbrlFact, parse_companyfacts
from marketdata.normalize.metrics import CALC_VERSION, MetricRow, reported_metrics

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts"
AAPL = parse_companyfacts((FIXTURES / "AAPL.json").read_bytes())
TEN_Q = "0000320193-26-000020"  # Apple's 10-Q for the quarter ended 2026-06-27
FILED = date(2026, 8, 1)


def _apple() -> dict[tuple[str, str, date], MetricRow]:
    built = reported_metrics(320193, AAPL.facts, fiscal_year_end="0926", sic_code="3571")
    return {row.key: row for row in built.rows}


def _fact(
    concept: str,
    start: date | None,
    end: date,
    value: int,
    *,
    form: str = "10-Q",
    accession: str = "0000000001-26-000010",
    filed: date = FILED,
    taxonomy: str = "us-gaap",
    unit: str = "USD",
) -> XbrlFact:
    return XbrlFact(
        cik=1,
        taxonomy=taxonomy,
        concept=concept,
        unit=unit,
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


Q2 = (date(2026, 4, 1), date(2026, 6, 30))
YEAR = (date(2025, 1, 1), date(2025, 12, 31))


def test_quarters_come_from_three_month_facts_with_their_filing() -> None:
    row = _apple()[("revenue", "Q", date(2026, 6, 27))]

    assert (row.fiscal_year, row.fiscal_period, row.period_start) == (2026, "Q3", date(2026, 3, 29))
    assert row.value == Decimal(109417000000)
    assert row.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert (row.accession_no, row.source_accessions, row.filed_at) == (
        TEN_Q,
        (TEN_Q,),
        date(2026, 7, 31),
    )
    assert (row.method, row.calc_version, row.quality_flags) == ("reported", CALC_VERSION, ())


def test_years_come_from_annual_facts_and_year_to_date_waits_for_e4b() -> None:
    rows = _apple()
    year = rows[("revenue", "FY", date(2025, 9, 27))]

    assert (year.fiscal_year, year.fiscal_period, year.period_start) == (
        2025,
        "FY",
        date(2024, 9, 29),
    )
    assert year.value == Decimal(416161000000)
    # The six and nine months to a quarter end are no row; no three-month Q4 either.
    assert ("revenue", "Q", date(2025, 9, 27)) not in rows
    assert {row.period_type for row in rows.values()} == {"Q", "FY"}


def test_the_latest_filing_wins() -> None:
    # The quarter to 2025-06-28 is in its own 10-Q and again in the 10-Q a year later.
    row = _apple()[("eps_diluted", "Q", date(2025, 6, 28))]

    assert (row.value, row.accession_no) == (Decimal("1.57"), TEN_Q)


def test_instants_sit_at_every_quarter_end_and_year_end() -> None:
    rows = _apple()
    q3 = rows[("total_assets", "Q", date(2026, 6, 27))]
    q4 = rows[("total_assets", "Q", date(2025, 9, 27))]
    year = rows[("total_assets", "FY", date(2025, 9, 27))]

    assert (q3.fiscal_period, q3.period_start, q3.value) == ("Q3", None, Decimal(383266000000))
    assert (q4.fiscal_period, year.fiscal_period, year.fiscal_year) == ("Q4", "FY", 2025)
    assert q4.value == year.value == Decimal(359241000000)


def test_the_cover_page_count_sits_at_its_filings_period_end() -> None:
    # Apple's 10-Q for the quarter to 2026-06-27 gives its shares as of 2026-07-17.
    row = _apple()[("shares_outstanding", "Q", date(2026, 6, 27))]

    assert (row.value, row.concept, row.accession_no) == (
        Decimal(14594180000),
        "dei:EntityCommonStockSharesOutstanding",
        TEN_Q,
    )


def test_only_the_10k_and_10q_family_counts() -> None:
    facts = [
        _fact("NetIncomeLoss", *Q2, 10),
        _fact("NetIncomeLoss", *Q2, 99, form="DEF 14A", accession="0000000001-26-000020"),
        _fact("NetIncomeLoss", *YEAR, 50, form="10-K", accession="0000000001-26-000002"),
    ]

    rows = reported_metrics(1, facts, fiscal_year_end="1231", sic_code="3571").rows

    assert [(row.period_type, row.value) for row in rows if row.metric == "net_income"] == [
        ("FY", Decimal(50)),
        ("Q", Decimal(10)),
    ]


def test_the_first_concept_with_a_fact_is_used_and_its_flag_kept() -> None:
    # Realty Income tags no NetIncomeLoss: the amount available to common comes before
    # ProfitLoss, which includes minority interests (D-082).
    facts = [
        _fact("ProfitLoss", *Q2, 370),
        _fact("NetIncomeLossAvailableToCommonStockholdersBasic", *Q2, 344),
        _fact("ProfitLoss", date(2026, 1, 1), date(2026, 3, 31), 320),
    ]

    rows = reported_metrics(1, facts, fiscal_year_end="1231", sic_code="6798").rows

    assert [(row.period_end, row.value, row.quality_flags) for row in rows] == [
        (date(2026, 3, 31), Decimal(320), ("net_income_includes_nci",)),
        (date(2026, 6, 30), Decimal(344), ("net_income_to_common",)),
    ]


def test_a_bank_has_no_revenue() -> None:
    facts = [
        _fact("RevenuesNetOfInterestExpense", *Q2, 47_000),
        _fact("NetIncomeLoss", *Q2, 15_000),
    ]

    rows = reported_metrics(19617, facts, fiscal_year_end="1231", sic_code="6021").rows

    revenue = [row for row in rows if row.metric in ("revenue", "gross_profit")]
    assert [(row.metric, row.value, row.quality_flags) for row in revenue] == [
        ("gross_profit", None, ("not_applicable_financials",)),
        ("revenue", None, ("not_applicable_financials",)),
    ]
    assert revenue[0].period_end == Q2[1] and revenue[0].concept is None


def test_two_quarters_ending_on_the_same_day_give_the_later_filing() -> None:
    stub = _fact("Revenues", date(2026, 4, 5), Q2[1], 5, accession="0000000001-26-000010")
    later = _fact("Revenues", *Q2, 7, accession="0000000001-26-000030", filed=date(2026, 9, 1))

    built = reported_metrics(1, [stub, later], fiscal_year_end="1231", sic_code="3571")

    [row] = [row for row in built.rows if row.metric == "revenue"]
    assert (row.value, row.period_start) == (Decimal(7), Q2[0])
    assert built.notes and "end on the same day" in built.notes[0]


def test_a_twelve_month_period_that_is_no_fiscal_year_gives_no_row() -> None:
    facts = [_fact("Revenues", date(2025, 7, 1), date(2026, 6, 30), 9)]

    built = reported_metrics(1, facts, fiscal_year_end="1231", sic_code="3571")

    assert built.rows == () and "no fiscal year" in built.notes[0]


def _row(value: int, accession: str, filed: date, calc_version: str = CALC_VERSION) -> MetricRow:
    return MetricRow(
        cik=1,
        metric="revenue",
        period_type="Q",
        fiscal_year=2026,
        fiscal_period="Q2",
        period_start=Q2[0],
        period_end=Q2[1],
        value=Decimal(value),
        unit="USD",
        concept="Revenues",
        accession_no=accession,
        source_accessions=(accession,),
        filed_at=filed,
        calc_version=calc_version,
    )


@pytest.mark.parametrize(
    ("new", "reason", "detail"),
    [
        (
            _row(8, "0000000001-27-000010", date(2027, 8, 1)),
            "restatement",
            "0000000001-27-000010, filed 2027-08-01, restates 0000000001-26-000010 of 2026-08-01",
        ),
        (_row(8, "0000000001-26-000010", date(2026, 8, 1), "9.9"), "correction", "calc version"),
        (_row(8, "0000000001-26-000010", date(2026, 8, 1)), "correction", "unchanged"),
    ],
)
def test_a_changed_value_says_why(new: MetricRow, reason: str, detail: str) -> None:
    stored = {
        new.key: StoredMetric(Decimal(7), "0000000001-26-000010", date(2026, 8, 1), CALC_VERSION)
    }

    [change] = metric_changes(stored, [new])

    assert (change.old_value, change.new_value, change.reason) == (Decimal(7), Decimal(8), reason)
    assert detail in change.detail
    assert change.entity_key == {
        "metric": "revenue",
        "period_type": "Q",
        "period_end": "2026-06-30",
    }


def test_an_unchanged_value_or_a_new_period_is_no_change() -> None:
    same = _row(7, "0000000001-27-000010", date(2027, 8, 1))  # a later filing, same value
    stored = {same.key: StoredMetric(Decimal(7), "0000000001-26-000010", date(2026, 8, 1), "1.1")}

    assert metric_changes(stored, [same]) == []
    assert metric_changes({}, [same]) == []
