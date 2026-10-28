"""E4b-S1: quarters derived from year-to-date facts (PLAN.md §9.4 rules 3-5), with synthetic
facts for each rule: six months less the first quarter, nine less six, the year less nine
months or less its first three quarters; one concept per derivation; a reported quarter wins;
no derived per-share or average-share values."""

from datetime import date
from decimal import Decimal

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.financials import build_metrics
from marketdata.normalize.metrics import (
    CONCEPT_MISMATCH,
    EPS_Q4_NOT_REPORTED,
    NOT_APPLICABLE_FINANCIALS,
    MetricRow,
    reported_metrics,
)

CASH = "NetCashProvidedByUsedInOperatingActivities"
PPE = "PaymentsToAcquirePropertyPlantAndEquipment"
PRODUCTIVE = "PaymentsToAcquireProductiveAssets"
Q1_10Q = "0000000001-26-000010"
Q2_10Q = "0000000001-26-000020"
Q3_10Q = "0000000001-26-000030"
TEN_K = "0000000001-27-000001"

YEAR = (date(2026, 1, 1), date(2026, 12, 31))
Q1 = (date(2026, 1, 1), date(2026, 3, 31))
SIX = (date(2026, 1, 1), date(2026, 6, 30))
NINE = (date(2026, 1, 1), date(2026, 9, 30))
Q2 = (date(2026, 4, 1), date(2026, 6, 30))
Q3 = (date(2026, 7, 1), date(2026, 9, 30))


def _fact(
    concept: str,
    period: tuple[date, date],
    value: int | str,
    accession: str,
    *,
    form: str = "10-Q",
    unit: str = "USD",
) -> XbrlFact:
    filed = {
        Q1_10Q: date(2026, 5, 1),
        Q2_10Q: date(2026, 8, 1),
        Q3_10Q: date(2026, 11, 1),
        TEN_K: date(2027, 2, 15),
    }[accession]
    return XbrlFact(
        cik=1,
        taxonomy="us-gaap",
        concept=concept,
        unit=unit,
        period_start=period[0],
        period_end=period[1],
        value=Decimal(value),
        fy=None,
        fp=None,
        form="10-K" if accession == TEN_K else form,
        frame=None,
        filed_at=filed,
        accession_no=accession,
    )


def _year_to_date(concept: str = CASH) -> list[XbrlFact]:
    """A cash-flow line as 10-Qs and a 10-K report it: three, six and nine months, the year."""
    return [
        _fact(concept, Q1, 100, Q1_10Q),
        _fact(concept, SIX, 250, Q2_10Q),
        _fact(concept, NINE, 330, Q3_10Q),
        _fact(concept, YEAR, 500, TEN_K),
    ]


def _rows(facts: list[XbrlFact], sic: str = "3571") -> dict[tuple[str, str, date], MetricRow]:
    built = build_metrics(1, facts, fiscal_year_end="1231", sic_code=sic)
    return {row.key: row for row in built.rows}


def test_the_first_quarter_is_reported_and_the_second_is_six_months_less_it() -> None:
    rows = _rows(_year_to_date())
    first = rows[("operating_cash_flow", "Q", Q1[1])]
    second = rows[("operating_cash_flow", "Q", Q2[1])]

    assert (first.method, first.value) == ("reported", Decimal(100))
    assert (second.method, second.value) == ("derived_ytd", Decimal(150))
    assert (second.period_start, second.fiscal_year, second.fiscal_period) == (Q2[0], 2026, "Q2")
    # The proof: the latest filing read, and every filing read.
    assert (second.accession_no, second.filed_at) == (Q2_10Q, date(2026, 8, 1))
    assert second.source_accessions == (Q1_10Q, Q2_10Q)
    assert second.concept == CASH


def test_the_third_quarter_is_nine_months_less_six() -> None:
    third = _rows(_year_to_date())[("operating_cash_flow", "Q", Q3[1])]

    assert (third.method, third.value, third.period_start) == ("derived_ytd", Decimal(80), Q3[0])
    assert third.source_accessions == (Q2_10Q, Q3_10Q)


def test_the_fourth_quarter_is_the_year_less_nine_months() -> None:
    fourth = _rows(_year_to_date())[("operating_cash_flow", "Q", YEAR[1])]

    assert (fourth.method, fourth.value) == ("derived_q4", Decimal(170))
    assert (fourth.period_start, fourth.fiscal_period) == (date(2026, 10, 1), "Q4")
    assert (fourth.accession_no, fourth.source_accessions) == (TEN_K, (Q3_10Q, TEN_K))


def test_without_nine_months_the_fourth_quarter_is_the_year_less_three_quarters() -> None:
    # Quarters reported as three months only (no year to date), then the year.
    facts = [
        _fact(CASH, Q1, 100, Q1_10Q),
        _fact(CASH, Q2, 150, Q2_10Q),
        _fact(CASH, Q3, 80, Q3_10Q),
        _fact(CASH, YEAR, 500, TEN_K),
    ]
    fourth = _rows(facts)[("operating_cash_flow", "Q", YEAR[1])]

    assert (fourth.method, fourth.value, fourth.period_start) == (
        "derived_q4",
        Decimal(170),
        date(2026, 10, 1),
    )
    assert fourth.source_accessions == (Q1_10Q, Q2_10Q, Q3_10Q, TEN_K)


def test_the_fallback_reads_a_quarter_that_is_itself_derived() -> None:
    # Six months but no nine: Q2 = 6M - Q1, Q3 reported, Q4 = year - (Q1 + Q2 + Q3).
    facts = [
        _fact(CASH, Q1, 100, Q1_10Q),
        _fact(CASH, SIX, 250, Q2_10Q),
        _fact(CASH, Q3, 80, Q3_10Q),
        _fact(CASH, YEAR, 500, TEN_K),
    ]
    rows = _rows(facts)

    assert rows[("operating_cash_flow", "Q", Q2[1])].value == Decimal(150)
    assert rows[("operating_cash_flow", "Q", YEAR[1])].value == Decimal(170)


def test_a_reported_three_month_quarter_is_never_replaced() -> None:
    # The 10-K tags the fourth quarter itself (Accenture, Target, D-084), 171 against the
    # 170 the year less nine months would give; and the second quarter is tagged too.
    facts = [
        *_year_to_date(),
        _fact(CASH, (date(2026, 10, 1), YEAR[1]), 171, TEN_K),
        _fact(CASH, Q2, 151, Q2_10Q),
    ]
    rows = _rows(facts)

    assert rows[("operating_cash_flow", "Q", YEAR[1])].value == Decimal(171)
    assert rows[("operating_cash_flow", "Q", YEAR[1])].method == "reported"
    assert rows[("operating_cash_flow", "Q", Q2[1])].value == Decimal(151)
    assert rows[("operating_cash_flow", "Q", Q2[1])].method == "reported"


def test_concepts_are_never_mixed_in_one_derivation() -> None:
    # Nine months under one capex concept, the year only under another: no fourth quarter.
    facts = [
        _fact(PPE, Q1, 10, Q1_10Q),
        _fact(PPE, SIX, 25, Q2_10Q),
        _fact(PPE, NINE, 40, Q3_10Q),
        _fact(PRODUCTIVE, YEAR, 60, TEN_K),
    ]
    rows = _rows(facts)
    fourth = rows[("capex", "Q", YEAR[1])]

    assert fourth.value is None
    assert fourth.quality_flags == (CONCEPT_MISMATCH,)
    assert (fourth.method, fourth.concept, fourth.source_accessions) == ("derived_q4", None, ())
    assert rows[("capex", "Q", Q3[1])].value == Decimal(15)  # nine less six, both PPE
    assert rows[("capex", "FY", YEAR[1])].value == Decimal(60)  # the year itself is reported


def test_one_concept_that_has_every_part_is_used() -> None:
    # The first concept reports only the year; the second reports nine months and the year,
    # so the fourth quarter reads the second concept alone.
    facts = [
        _fact(PPE, YEAR, 61, TEN_K),
        _fact(PRODUCTIVE, NINE, 40, Q3_10Q),
        _fact(PRODUCTIVE, YEAR, 60, TEN_K),
    ]
    rows = _rows(facts)
    fourth = rows[("capex", "Q", YEAR[1])]

    assert (fourth.value, fourth.concept, fourth.method) == (Decimal(20), PRODUCTIVE, "derived_q4")
    assert rows[("capex", "FY", YEAR[1])].value == Decimal(61)  # the year keeps the first concept


def test_per_share_and_average_share_metrics_are_never_derived() -> None:
    facts = [
        _fact("EarningsPerShareDiluted", Q1, "1.00", Q1_10Q, unit="USD/shares"),
        _fact("EarningsPerShareDiluted", SIX, "2.10", Q2_10Q, unit="USD/shares"),
        _fact("EarningsPerShareDiluted", NINE, "3.00", Q3_10Q, unit="USD/shares"),
        _fact("EarningsPerShareDiluted", YEAR, "4.20", TEN_K, unit="USD/shares"),
        _fact("WeightedAverageNumberOfDilutedSharesOutstanding", NINE, 100, Q3_10Q, unit="shares"),
        _fact("WeightedAverageNumberOfDilutedSharesOutstanding", YEAR, 101, TEN_K, unit="shares"),
    ]
    rows = _rows(facts)
    fourth = rows[("eps_diluted", "Q", YEAR[1])]

    assert ("eps_diluted", "Q", Q2[1]) not in rows  # six months less the first quarter: no
    assert fourth.value is None
    assert (fourth.quality_flags, fourth.method) == ((EPS_Q4_NOT_REPORTED,), "derived_q4")
    assert fourth.period_start == date(2026, 10, 1)  # after the nine months
    shares = rows[("shares_diluted_avg", "Q", YEAR[1])]
    assert (shares.value, shares.quality_flags) == (None, (EPS_Q4_NOT_REPORTED,))


def test_a_reported_fourth_quarter_eps_is_kept() -> None:
    facts = [
        _fact("EarningsPerShareDiluted", YEAR, "4.20", TEN_K, unit="USD/shares"),
        _fact(
            "EarningsPerShareDiluted",
            (date(2026, 10, 1), YEAR[1]),
            "1.20",
            TEN_K,
            unit="USD/shares",
        ),
    ]
    fourth = _rows(facts)[("eps_diluted", "Q", YEAR[1])]

    assert (fourth.value, fourth.method, fourth.quality_flags) == (Decimal("1.20"), "reported", ())


def test_a_difference_that_is_no_quarter_is_not_made() -> None:
    # Three and nine months only: nine less three is six months.
    facts = [_fact(CASH, Q1, 100, Q1_10Q), _fact(CASH, NINE, 330, Q3_10Q)]
    rows = _rows(facts)

    assert [key for key in rows if key[0] == "operating_cash_flow"] == [
        ("operating_cash_flow", "Q", Q1[1])
    ]


def test_costcos_twelve_and_sixteen_week_quarters() -> None:
    # 12-week first quarter, 24 and 36 weeks to date, a 52-week year: a 16-week fourth quarter.
    start = date(2025, 9, 1)
    facts = [
        _fact(CASH, (start, date(2025, 11, 23)), 12, Q1_10Q),
        _fact(CASH, (start, date(2026, 2, 15)), 24, Q2_10Q),
        _fact(CASH, (start, date(2026, 5, 10)), 36, Q3_10Q),
        _fact(CASH, (start, date(2026, 8, 30)), 52, TEN_K),
    ]
    built = build_metrics(1, facts, fiscal_year_end="0830", sic_code="5331")
    quarters = {
        row.period_end: row
        for row in built.rows
        if row.metric == "operating_cash_flow" and row.period_type == "Q"
    }

    assert [(row.fiscal_period, row.value, row.method) for row in quarters.values()] == [
        ("Q1", Decimal(12), "reported"),
        ("Q2", Decimal(12), "derived_ytd"),
        ("Q3", Decimal(12), "derived_ytd"),
        ("Q4", Decimal(16), "derived_q4"),
    ]


def test_a_concepts_flag_carries_into_its_derived_quarters() -> None:
    facts = [
        _fact("ProfitLoss", Q1, 10, Q1_10Q),
        _fact("ProfitLoss", SIX, 25, Q2_10Q),
    ]
    second = _rows(facts)[("net_income", "Q", Q2[1])]

    assert (second.value, second.quality_flags) == (Decimal(15), ("net_income_includes_nci",))


def test_a_banks_revenue_is_not_applicable_in_derived_quarters_too() -> None:
    rows = _rows(_year_to_date(), sic="6021")
    revenue = rows[("revenue", "Q", Q3[1])]

    assert (revenue.value, revenue.quality_flags) == (None, (NOT_APPLICABLE_FINANCIALS,))


def test_e4as_reported_rows_have_no_derived_quarters() -> None:
    rows = reported_metrics(1, _year_to_date(), fiscal_year_end="1231", sic_code="3571").rows

    assert {row.method for row in rows} == {"reported"}
    assert [row.period_end for row in rows if row.period_type == "Q"] == [Q1[1]]
