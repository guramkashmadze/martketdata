"""E4b-S3: every §9.5 quality check, each with a test that triggers it. The checks E4b adds are
triggered here; the others are triggered where their value is made, and INDEX names each
test. A flag never changes or hides a value."""

import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.financials import build_metrics
from marketdata.normalize.metrics import NON_USD_REPORTER, MetricRow
from marketdata.normalize.quality import (
    BALANCE_MISMATCH,
    DESCRIPTIONS,
    OUTLIER_JUMP,
    QUARTERS_DONT_SUM,
    STALE_DATA,
    is_stale,
)

UNIT_TESTS = Path(__file__).resolve().parent
QUARTERS = [
    (date(2025, 1, 1), date(2025, 3, 31)),
    (date(2025, 4, 1), date(2025, 6, 30)),
    (date(2025, 7, 1), date(2025, 9, 30)),
    (date(2025, 10, 1), date(2025, 12, 31)),
]
YEAR = (date(2025, 1, 1), date(2025, 12, 31))

# Each §9.5 code and the test that triggers it (file::function).
INDEX = {
    BALANCE_MISMATCH: "test_quality_checks.py::test_balance_mismatch",
    QUARTERS_DONT_SUM: "test_quality_checks.py::test_quarters_dont_sum",
    OUTLIER_JUMP: "test_quality_checks.py::test_outlier_jump",
    "concept_mismatch": "test_derived_metrics.py::test_concepts_are_never_mixed_in_one_derivation",
    "ttm_gap": "test_computed_metrics.py::test_a_missing_quarter_is_a_ttm_gap",
    STALE_DATA: "test_quality_checks.py::test_stale_data",
    "eps_q4_not_reported": (
        "test_derived_metrics.py::test_per_share_and_average_share_metrics_are_never_derived"
    ),
    "net_income_includes_nci": (
        "test_reported_metrics.py::test_the_first_concept_with_a_fact_is_used_and_its_flag_kept"
    ),
    "net_income_to_common": (
        "test_reported_metrics.py::test_the_first_concept_with_a_fact_is_used_and_its_flag_kept"
    ),
    "cash_includes_restricted": "test_quality_checks.py::test_cash_includes_restricted",
    NON_USD_REPORTER: "test_quality_checks.py::test_non_usd_reporter",
    "not_applicable_financials": "test_reported_metrics.py::test_a_bank_has_no_revenue",
    "scale_unknown": "test_earnings_rules.py::test_no_scale_fails_the_money_slots_but_not_eps",
    "sign_change": "test_earnings_assembly.py::test_a_sign_change_has_no_change_percent",
    "release_vs_filing_mismatch": (
        "test_cross_check.py::test_a_quarter_whose_numbers_changed_between_release_and_filing"
    ),
}
LATER = {"price_not_licensed": "E10: prices are not loaded yet"}


def _fact(
    concept: str,
    period: tuple[date, date] | date,
    value: int | str,
    *,
    unit: str = "USD",
    form: str = "10-Q",
) -> XbrlFact:
    start, end = period if isinstance(period, tuple) else (None, period)
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
        filed_at=end + timedelta(days=30),
        accession_no=f"0000000001-{end:%y}-{end.month:06d}",
    )


def _rows(facts: list[XbrlFact]) -> dict[tuple[str, str, date], MetricRow]:
    built = build_metrics(1, facts, fiscal_year_end="1231", sic_code="3571")
    return {row.key: row for row in built.rows}


def test_every_code_in_section_9_5_has_a_test_that_triggers_it() -> None:
    assert set(INDEX) | set(LATER) == set(DESCRIPTIONS)
    for code, test in INDEX.items():
        name, function = test.split("::")
        text = (UNIT_TESTS / name).read_text(encoding="utf-8")
        assert re.search(rf"^def {function}\(", text, re.MULTILINE), (code, test)
        assert code in text or code.upper() in text, (code, test)


def test_balance_mismatch() -> None:
    end = QUARTERS[0][1]
    facts = [
        _fact("Revenues", QUARTERS[0], 10),
        _fact("Assets", end, 1000),
        _fact("LiabilitiesAndStockholdersEquity", end, 1020),  # 2% off
        # Equity including the noncontrolling interest: total liabilities are computed too.
        _fact("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", end, 400),
    ]
    rows = _rows(facts)

    for metric in ("total_assets", "total_equity"):
        row = rows[(metric, "Q", end)]
        assert BALANCE_MISMATCH in row.quality_flags
        assert row.value is not None  # a flag never hides a value
    assert rows[("total_liabilities", "Q", end)].quality_flags == (BALANCE_MISMATCH,)

    within = [*facts[:2], _fact("LiabilitiesAndStockholdersEquity", end, 1009), facts[3]]
    assert BALANCE_MISMATCH not in _rows(within)[("total_assets", "Q", end)].quality_flags


def test_quarters_dont_sum() -> None:
    quarters = [_fact("Revenues", period, 25) for period in QUARTERS]
    facts = [*quarters, _fact("Revenues", YEAR, 110, form="10-K")]
    rows = _rows(facts)

    assert QUARTERS_DONT_SUM in rows[("revenue", "FY", YEAR[1])].quality_flags
    for _, end in QUARTERS:
        assert QUARTERS_DONT_SUM in rows[("revenue", "Q", end)].quality_flags

    agreeing = [*quarters, _fact("Revenues", YEAR, "100.4", form="10-K")]  # within 0.5%
    assert not _rows(agreeing)[("revenue", "FY", YEAR[1])].quality_flags


def test_quarters_dont_sum_needs_four_reported_quarters_and_skips_per_share() -> None:
    # A derived fourth quarter sums to the year by construction.
    derived = [
        *(_fact("Revenues", period, 25) for period in QUARTERS[:3]),
        _fact("Revenues", (YEAR[0], QUARTERS[2][1]), 75),
        _fact("Revenues", YEAR, 110, form="10-K"),
    ]
    assert QUARTERS_DONT_SUM not in _rows(derived)[("revenue", "FY", YEAR[1])].quality_flags
    # A year's earnings per share is no sum of its quarters'.
    eps = [
        *(
            _fact("EarningsPerShareDiluted", period, "0.25", unit="USD/shares")
            for period in QUARTERS
        ),
        _fact("EarningsPerShareDiluted", YEAR, "1.10", unit="USD/shares", form="10-K"),
    ]
    assert not _rows(eps)[("eps_diluted", "FY", YEAR[1])].quality_flags


def test_outlier_jump() -> None:
    facts = [
        _fact("Revenues", QUARTERS[0], 100),
        _fact("Revenues", QUARTERS[1], 400),  # four times: not flagged
        _fact("Revenues", QUARTERS[2], 2100),  # more than five times
        _fact("Revenues", QUARTERS[3], 400),  # under a fifth
    ]
    rows = _rows(facts)

    assert [OUTLIER_JUMP in rows[("revenue", "Q", end)].quality_flags for _, end in QUARTERS] == [
        False,
        False,
        True,
        True,
    ]
    assert rows[("revenue", "Q", QUARTERS[2][1])].value == Decimal(2100)


def test_outlier_jump_skips_zero_and_percentages() -> None:
    facts = [
        _fact("Revenues", QUARTERS[0], 100),
        _fact("Revenues", QUARTERS[1], 100),
        _fact("NetIncomeLoss", QUARTERS[0], 0),
        _fact("NetIncomeLoss", QUARTERS[1], 30),  # from zero: not compared
    ]
    rows = _rows(facts)

    assert not rows[("net_income", "Q", QUARTERS[1][1])].quality_flags
    # The net margin goes from 0% to 30%: percentages are not compared.
    assert not rows[("net_margin", "Q", QUARTERS[1][1])].quality_flags


def test_stale_data() -> None:
    today = date(2026, 10, 29)

    assert is_stale(today - timedelta(days=121), today)
    assert not is_stale(today - timedelta(days=120), today)
    assert is_stale(None, today)  # no filing with facts at all


def test_cash_includes_restricted() -> None:
    end = QUARTERS[0][1]
    facts = [
        _fact("Revenues", QUARTERS[0], 10),
        _fact("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", end, 75),
    ]
    row = _rows(facts)[("cash", "Q", end)]

    assert (row.value, row.quality_flags) == (Decimal(75), ("cash_includes_restricted",))


def test_non_usd_reporter() -> None:
    # A domestic filer reporting in euros: its money metrics are skipped (§9.4 rule 9), each
    # period null with the flag; share counts are kept.
    facts = [
        _fact("Revenues", QUARTERS[0], 500, unit="EUR"),
        _fact("EarningsPerShareDiluted", QUARTERS[0], "1.20", unit="EUR/shares"),
        _fact("Assets", QUARTERS[0][1], 9000, unit="EUR"),
        _fact("WeightedAverageNumberOfDilutedSharesOutstanding", QUARTERS[0], 400, unit="shares"),
    ]
    rows = _rows(facts)
    end = QUARTERS[0][1]

    for metric in ("revenue", "eps_diluted", "total_assets"):
        row = rows[(metric, "Q", end)]
        assert (row.value, row.quality_flags) == (None, (NON_USD_REPORTER,)), metric
    assert rows[("shares_diluted_avg", "Q", end)].value == Decimal(400)


@pytest.mark.parametrize("code", sorted(DESCRIPTIONS))
def test_every_code_is_described(code: str) -> None:
    assert DESCRIPTIONS[code] and not DESCRIPTIONS[code].endswith(".")
