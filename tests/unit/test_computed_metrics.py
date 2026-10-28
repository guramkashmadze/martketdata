"""E4b-S2: TTM (PLAN.md §9.4 rule 8) and the computed metrics of §9.3: free cash flow,
margins, year-over-year growth, gross profit and total liabilities where none is tagged; with
synthetic facts for gaps, contiguity and the rules' edges."""

from datetime import date, timedelta
from decimal import Decimal

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.computed import SIGN_CHANGE, TTM_GAP
from marketdata.normalize.financials import build_metrics
from marketdata.normalize.metric_catalog import CATALOG_VERSION, COMPUTED, CORE_COMPUTED, UNITS
from marketdata.normalize.metrics import (
    EPS_Q4_NOT_REPORTED,
    NOT_APPLICABLE_FINANCIALS,
    MetricRow,
)

DAY = timedelta(days=1)
# Calendar quarters of 2025 and 2026, each reported three months long in its own 10-Q.
QUARTERS = [
    (date(2025, 1, 1), date(2025, 3, 31)),
    (date(2025, 4, 1), date(2025, 6, 30)),
    (date(2025, 7, 1), date(2025, 9, 30)),
    (date(2025, 10, 1), date(2025, 12, 31)),
    (date(2026, 1, 1), date(2026, 3, 31)),
    (date(2026, 4, 1), date(2026, 6, 30)),
]
INSTANT = "instant"


def _fact(
    concept: str,
    period: tuple[date, date] | date,
    value: int | str,
    *,
    unit: str = "USD",
    form: str = "10-Q",
    accession: str | None = None,
) -> XbrlFact:
    start, end = period if isinstance(period, tuple) else (None, period)
    filed = end + timedelta(days=30)
    return XbrlFact(
        cik=1,
        taxonomy="us-gaap",
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
        accession_no=accession or f"0000000001-{end:%y}-{end.month:06d}",
    )


def _quarterly(concept: str, values: list[int | str], unit: str = "USD") -> list[XbrlFact]:
    return [
        _fact(concept, period, value, unit=unit)
        for period, value in zip(QUARTERS, values, strict=False)
        if value is not None
    ]


def _year_ends() -> list[XbrlFact]:
    """A 10-K for 2025, so the calendar knows its year ends."""
    return [_fact("Revenues", (date(2025, 1, 1), date(2025, 12, 31)), 1000, form="10-K")]


def _rows(facts: list[XbrlFact], sic: str = "3571") -> dict[tuple[str, str, date], MetricRow]:
    built = build_metrics(1, facts, fiscal_year_end="1231", sic_code=sic)
    return {row.key: row for row in built.rows}


def test_ttm_is_the_sum_of_four_contiguous_quarters() -> None:
    facts = [*_year_ends(), *_quarterly("Revenues", [100, 110, 120, 670, 130, 140])]
    rows = _rows(facts)
    ttm = rows[("revenue", "TTM", date(2026, 3, 31))]

    assert ttm.value == Decimal(110 + 120 + 670 + 130)
    assert (ttm.method, ttm.period_start, ttm.fiscal_year, ttm.fiscal_period) == (
        "derived_ttm",
        date(2025, 4, 1),
        2026,
        "Q1",
    )
    assert ttm.accession_no == "0000000001-26-000003"  # the latest quarter's filing
    assert ttm.concept == "Revenues"
    # A quarter end has a TTM once four quarters reach it.
    assert ("revenue", "TTM", date(2025, 9, 30)) not in rows
    assert rows[("revenue", "TTM", date(2025, 12, 31))].value == Decimal(1000)


def test_a_missing_quarter_is_a_ttm_gap() -> None:
    # Without the third quarter (or nine months) the 2025 year can't give the fourth either.
    facts = [*_year_ends(), *_quarterly("Revenues", [100, 110, None, None, 130, 140])]
    rows = _rows(facts)
    ttm = rows[("revenue", "TTM", date(2026, 6, 30))]

    assert (ttm.value, ttm.quality_flags) == (None, (TTM_GAP,))
    assert (ttm.accession_no, ttm.source_accessions) == (None, ())


def test_quarters_that_dont_follow_each_other_are_a_ttm_gap() -> None:
    # Four quarters on file, but the third of 2025 is missing between them.
    facts = _quarterly("Revenues", [100, 110, None, 120, 130])
    rows = _rows(facts)

    assert rows[("revenue", "TTM", date(2026, 3, 31))].quality_flags == (TTM_GAP,)


def test_a_null_quarter_makes_the_ttm_null_with_its_flag() -> None:
    eps = "EarningsPerShareDiluted"
    facts = [
        *_quarterly(eps, ["1.00", "1.10", "1.20"], unit="USD/shares"),
        _fact(eps, (date(2025, 1, 1), date(2025, 9, 30)), "3.30", unit="USD/shares"),
        _fact(eps, (date(2025, 1, 1), date(2025, 12, 31)), "4.60", unit="USD/shares", form="10-K"),
        _fact(eps, QUARTERS[4], "1.40", unit="USD/shares"),
    ]
    ttm = _rows(facts)[("eps_diluted", "TTM", date(2026, 3, 31))]

    assert ttm.value is None
    assert ttm.quality_flags == (TTM_GAP, EPS_Q4_NOT_REPORTED)


def test_a_banks_revenue_ttm_is_not_applicable_not_a_gap() -> None:
    facts = [*_year_ends(), *_quarterly("NetIncomeLoss", [10, 11, 12, 13, 14])]
    ttm = _rows(facts, sic="6021")[("revenue", "TTM", date(2026, 3, 31))]

    assert (ttm.value, ttm.quality_flags) == (None, (NOT_APPLICABLE_FINANCIALS,))


def test_an_instants_ttm_is_its_quarter_end_value() -> None:
    # A quarter end is the end of a period the company reports (E4a-S4).
    facts = [
        *_quarterly("Revenues", [1, 1, 1, 1, 1, 1]),
        *(_fact("Assets", end, 500 + index) for index, (_, end) in enumerate(QUARTERS)),
    ]
    rows = _rows(facts)

    for index, (_, end) in enumerate(QUARTERS):
        ttm = rows[("total_assets", "TTM", end)]
        assert (ttm.value, ttm.method, ttm.period_start) == (Decimal(500 + index), "reported", None)


def test_average_shares_have_no_ttm() -> None:
    facts = _quarterly(
        "WeightedAverageNumberOfDilutedSharesOutstanding", [100, 100, 100, 100], unit="shares"
    )

    assert not [key for key in _rows(facts) if key[1] == "TTM"]


def test_free_cash_flow_is_operating_cash_flow_less_capex() -> None:
    facts = [
        *_quarterly("NetCashProvidedByUsedInOperatingActivities", [50, 60, 70, 80]),
        *_quarterly("PaymentsToAcquirePropertyPlantAndEquipment", [5, 6, 7, 8]),
    ]
    rows = _rows(facts)
    quarter = rows[("free_cash_flow", "Q", date(2025, 6, 30))]
    ttm = rows[("free_cash_flow", "TTM", date(2025, 12, 31))]

    assert (quarter.value, quarter.method, quarter.concept, quarter.unit) == (
        Decimal(54),
        "computed",
        None,
        "USD",
    )
    assert quarter.period_start == date(2025, 4, 1)
    assert ttm.value == Decimal(260 - 26)


def test_margins_are_percentages_of_revenue_to_one_decimal() -> None:
    facts = [
        *_quarterly("Revenues", [300]),
        *_quarterly("GrossProfit", [100]),
        *_quarterly("OperatingIncomeLoss", [-20]),
        *_quarterly("NetIncomeLossAvailableToCommonStockholdersBasic", [7]),
    ]
    rows = _rows(facts)
    end = QUARTERS[0][1]

    assert rows[("gross_margin", "Q", end)].value == Decimal("33.3")
    assert rows[("operating_margin", "Q", end)].value == Decimal("-6.7")
    net = rows[("net_margin", "Q", end)]
    assert (net.value, net.unit) == (Decimal("2.3"), "percent")
    # A value computed from net income to common carries that flag (§9.5).
    assert net.quality_flags == ("net_income_to_common",)


def test_no_margin_without_positive_revenue() -> None:
    facts = [*_quarterly("Revenues", [0]), *_quarterly("NetIncomeLoss", [-5])]

    assert ("net_margin", "Q", QUARTERS[0][1]) not in _rows(facts)


def test_growth_is_against_the_same_period_a_year_earlier() -> None:
    facts = [*_quarterly("Revenues", [100, 110, 120, 130, 125, 99])]
    rows = _rows(facts)

    assert rows[("revenue_growth_yoy", "Q", date(2026, 3, 31))].value == Decimal("25.0")
    assert rows[("revenue_growth_yoy", "Q", date(2026, 6, 30))].value == Decimal("-10.0")
    assert ("revenue_growth_yoy", "Q", date(2025, 12, 31)) not in rows  # no year before


def test_growth_across_a_53_week_year_still_finds_the_year_before() -> None:
    eps = "EarningsPerShareDiluted"
    before = (date(2024, 12, 30), date(2025, 3, 30))
    after = (date(2025, 12, 29), date(2026, 4, 5))  # 371 days later: a 53rd week
    facts = [
        _fact(eps, before, "2.00", unit="USD/shares"),
        _fact(eps, after, "2.50", unit="USD/shares"),
    ]
    growth = _rows(facts)[("eps_diluted_growth_yoy", "Q", after[1])]

    assert growth.value == Decimal("25.0")
    assert growth.source_accessions == ("0000000001-26-000004", "0000000001-25-000003")


def test_growth_across_a_change_of_sign_is_null_with_sign_change() -> None:
    eps = "EarningsPerShareDiluted"
    facts = [
        _fact(eps, QUARTERS[0], "-0.50", unit="USD/shares"),
        _fact(eps, QUARTERS[4], "0.20", unit="USD/shares"),
    ]
    growth = _rows(facts)[("eps_diluted_growth_yoy", "Q", QUARTERS[4][1])]

    assert (growth.value, growth.quality_flags) == (None, (SIGN_CHANGE,))


def test_no_growth_from_zero() -> None:
    facts = [*_quarterly("Revenues", [0, 1, 1, 1, 50])]

    assert ("revenue_growth_yoy", "Q", QUARTERS[4][1]) not in _rows(facts)


def test_a_banks_margins_and_growth_are_not_applicable() -> None:
    facts = [*_year_ends(), *_quarterly("NetIncomeLoss", [10, 11, 12, 13, 14])]
    rows = _rows(facts, sic="6021")
    margin = rows[("net_margin", "Q", QUARTERS[4][1])]
    growth = rows[("revenue_growth_yoy", "Q", QUARTERS[4][1])]

    assert (margin.value, margin.quality_flags) == (None, (NOT_APPLICABLE_FINANCIALS,))
    assert (growth.value, growth.quality_flags) == (None, (NOT_APPLICABLE_FINANCIALS,))


def test_gross_profit_is_computed_only_where_none_is_tagged() -> None:
    facts = [
        *_quarterly("Revenues", [300, 310]),
        *_quarterly("CostOfRevenue", [200, 205]),
        _fact("GrossProfit", QUARTERS[1], 104),  # tagged for the second quarter only
    ]
    rows = _rows(facts)
    first = rows[("gross_profit", "Q", QUARTERS[0][1])]
    second = rows[("gross_profit", "Q", QUARTERS[1][1])]

    assert (first.value, first.method, first.concept) == (Decimal(100), "computed", None)
    assert (second.value, second.method, second.concept) == (
        Decimal(104),
        "reported",
        "GrossProfit",
    )


def test_total_liabilities_are_computed_where_none_are_tagged() -> None:
    end = QUARTERS[0][1]
    facts = [
        _fact("Revenues", QUARTERS[0], 10),
        _fact("LiabilitiesAndStockholdersEquity", end, 900),
        _fact("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", end, 350),
        _fact("Assets", end, 900),
    ]
    row = _rows(facts)[("total_liabilities", "Q", end)]

    assert (row.value, row.method, row.concept, row.period_start) == (
        Decimal(550),
        "computed",
        None,
        None,
    )


def test_the_catalog_lists_the_computed_metrics() -> None:
    assert CATALOG_VERSION == "2"
    assert [metric.name for metric in COMPUTED] == [
        "free_cash_flow",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "revenue_growth_yoy",
        "eps_diluted_growth_yoy",
    ]
    assert CORE_COMPUTED == {metric.name for metric in COMPUTED}
    assert UNITS["free_cash_flow"] == "USD"
    assert {UNITS[metric.name] for metric in COMPUTED[1:]} == {"percent"}
