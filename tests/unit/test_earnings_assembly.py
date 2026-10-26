"""E7a-S4: from an extraction to verified rows (PLAN.md §9.9). Each crafted error in an
extraction (wrong scale, a value not in the source, a nine-month cell, an EPS that doesn't
match net income) fails its slot only; a sign change and a missing prior year pass with the
change left null."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from release_fixtures import fixture_release

from marketdata.extract.earnings_rules import RuleExtractor
from marketdata.extract.interface import ExtractionResult, Figure, Slot, SlotResult
from marketdata.normalize.earnings import (
    BANK_REASON,
    Change,
    ReleaseRecord,
    assemble,
    change,
    release_status,
    revenue_not_applicable,
)

APPLE = (320193, "0000320193-26-000018")
ACCEPTED = datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
M = Decimal(1_000_000)


def _apple() -> tuple[ExtractionResult, dict[str, str]]:
    release = fixture_release(*APPLE)
    texts = {document.doc_name: document.text for document in release.documents}
    return RuleExtractor().extract(release), texts


def _assemble(result: ExtractionResult, texts: dict[str, str]) -> ReleaseRecord:
    return assemble(
        result,
        texts,
        accession_no=APPLE[1],
        cik=APPLE[0],
        accepted_at=ACCEPTED,
        fiscal_year_end="0926",
    )


def _with_slot(result: ExtractionResult, slot: Slot, **changes: object) -> ExtractionResult:
    slots = tuple(
        replace(found, **changes) if found.slot == slot else found  # type: ignore[arg-type]
        for found in result.slots
    )
    return replace(result, slots=slots)


def _figure(found: SlotResult, **changes: object) -> Figure:
    assert found.current is not None
    return replace(found.current, **changes)  # type: ignore[arg-type]


def test_apples_release_is_complete() -> None:
    record = _assemble(*_apple())

    assert (record.status, record.fiscal_year, record.fiscal_period) == ("complete", 2026, "Q3")
    assert record.period_end == date(2026, 6, 27)
    revenue = record.fact("revenue")
    assert (revenue.check_status, revenue.value, revenue.prior_value) == (
        "passed",
        Decimal(109_417) * M,
        Decimal(94_036) * M,
    )
    assert revenue.change_pct == Decimal("16.4")
    assert revenue.source_quote == "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |"
    eps = record.fact("eps_diluted")
    assert (eps.value, eps.prior_value, eps.change_pct) == (
        Decimal("2.02"),
        Decimal("1.57"),
        Decimal("28.7"),
    )
    assert record.check_report["slots"]["revenue"]["checks"] == ["in_source", "scale", "sanity"]


def test_a_wrong_scale_fails_the_slot_only() -> None:
    result, texts = _apple()
    revenue = result.slot("revenue")
    wrong = _figure(revenue, value=Decimal(109_417) * 1000, scale=Decimal(1000))

    record = _assemble(_with_slot(result, "revenue", current=wrong), texts)

    fact = record.fact("revenue")
    assert (fact.check_status, fact.value, fact.prior_value) == ("failed", None, None)
    assert fact.quality_flags == ("scale_mismatch",)
    assert record.status == "partial"
    assert record.fact("net_income").check_status == "passed"


def test_a_value_not_in_the_source_fails() -> None:
    result, texts = _apple()
    net = result.slot("net_income")
    invented = _figure(net, cell="$ 29,790", value=Decimal(29_790) * M)

    record = _assemble(_with_slot(result, "net_income", current=invented), texts)

    assert record.fact("net_income").quality_flags == ("not_in_source",)
    # Without a verified net income the EPS check has nothing to compare with; EPS still passes.
    assert record.fact("eps_diluted").check_status == "passed"


def test_a_nine_month_cell_claimed_for_the_quarter_fails_the_eps_check() -> None:
    result, texts = _apple()
    eps = result.slot("eps_diluted")
    nine_months = _figure(eps, cell="$ 6.88", value=Decimal("6.88"))

    record = _assemble(_with_slot(result, "eps_diluted", current=nine_months), texts)

    assert record.fact("eps_diluted").quality_flags == ("eps_net_income_mismatch",)


def test_a_sign_change_has_no_change_percent() -> None:
    result, texts = _apple()
    other = "| Other income/(expense), net | 572 | (171) | 670 | (698) |"
    operating = result.slot("operating_income")
    current = _figure(operating, cell="572", value=Decimal(572) * M, quote=other)
    prior = _figure(operating, cell="(171)", value=Decimal(-171) * M, quote=other)

    record = _assemble(_with_slot(result, "operating_income", current=current, prior=prior), texts)

    fact = record.fact("operating_income")
    assert (fact.check_status, fact.change_pct, fact.quality_flags) == (
        "passed",
        None,
        ("sign_change",),
    )


def test_a_missing_prior_year_passes_without_a_change() -> None:
    result, texts = _apple()

    record = _assemble(_with_slot(result, "revenue", prior=None), texts)

    fact = record.fact("revenue")
    assert (fact.check_status, fact.prior_value, fact.change_pct) == ("passed", None, None)


def test_a_change_above_1000_percent_fails() -> None:
    result, texts = _apple()
    row = "| Products | 47,153 | 43,620 | 163,810 | 147,097 |"
    revenue = result.slot("revenue")
    current = _figure(revenue, cell="163,810", value=Decimal(163_810) * M, quote=row)
    prior = _figure(revenue, cell="$ 1.57", value=Decimal("1.57") * M)

    record = _assemble(_with_slot(result, "revenue", current=current, prior=prior), texts)

    assert "change_over_1000_pct" in record.fact("revenue").quality_flags


@pytest.mark.parametrize(
    ("statuses", "lines", "expected"),
    [
        (["passed"] * 4, True, "complete"),
        (["not_applicable", "passed", "passed", "passed"], True, "complete"),  # a bank
        (["passed", "not_found", "passed", "failed"], True, "partial"),
        (["failed", "not_found", "not_found", "not_found"], True, "failed"),
        (["not_found"] * 4, False, "no_statement"),
    ],
)
def test_release_status(statuses: list[str], lines: bool, expected: str) -> None:
    assert release_status(statuses, lines) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "prior", "expected"),
    [
        ("109417", "94036", Change(Decimal("16.4"))),
        ("-5", "10", Change(None, ("sign_change",))),
        ("5", "-10", Change(None, ("sign_change",))),
        ("-12", "-10", Change(Decimal("-20.0"))),  # a larger loss
        ("5", "0", Change(None)),
        ("1.005", "1", Change(Decimal("0.5"))),  # half up
    ],
)
def test_change(value: str, prior: str, expected: Change) -> None:
    assert change(Decimal(value), Decimal(prior)) == expected


@pytest.mark.parametrize(
    ("sic", "reason"),
    [("6021", BANK_REASON), ("6022", BANK_REASON), ("6324", None), ("6331", None),
     ("6798", None), ("6199", None), (None, None), ("", None)],
)  # fmt: skip
def test_only_banks_lose_the_revenue_slot(sic: str | None, reason: str | None) -> None:
    assert revenue_not_applicable(sic) == reason
