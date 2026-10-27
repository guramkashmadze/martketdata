"""E7a-S5: the hit-rate report (PLAN.md §9.9) counts the slots of the stored releases per slot
and per company, with the overrides they needed. Slots that don't apply are counted apart, and
filings without a statement are listed for a check by hand, not counted."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from marketdata.cli import _report_lines
from marketdata.db.repositories.earnings import StoredFact, StoredRelease
from marketdata.service.extraction import SlotCounts, tally

NOW = datetime(2026, 10, 26, 20, 30, tzinfo=UTC)
SLOTS = ("revenue", "operating_income", "net_income", "eps_diluted")
APPLE, JPM, XOM, EXXON = 320193, 19617, 2115436, 34088  # 34088 is XOM's predecessor
OVERRIDE = "operating_income: not applicable (JPMorgan presents no operating income line)"


def _release(accession: str, cik: int, status: str, **report: Any) -> StoredRelease:
    return StoredRelease(
        accession_no=accession,
        cik=cik,
        accepted_at=NOW,
        doc_name="ex991.htm",
        fiscal_year=2026,
        fiscal_period="Q3",
        period_end=None,
        period_label=None,
        status=status,
        extractor="rules",
        extractor_version="2",
        check_report=report,
        verified_at=None,
        updated_at=NOW,
    )


def _facts(accession: str, *statuses: str, flag: str = "change_over_1000_pct") -> list[StoredFact]:
    return [
        StoredFact(
            accession_no=accession,
            slot=slot,
            value=Decimal(1) if status == "passed" else None,
            unit="USD",
            prior_value=None,
            change_pct=None,
            basis="GAAP",
            source_row_label=None,
            source_quote=None,
            scale_text=None,
            method="rule",
            extractor_version="2",
            check_status=status,
            cross_check_status="pending",
            quality_flags=[flag] if status == "failed" else [],
        )
        for slot, status in zip(SLOTS, statuses, strict=True)
    ]


def _report() -> Any:
    releases = [
        _release("0000320193-26-000001", APPLE, "complete"),
        _release("0000320193-26-000002", APPLE, "partial"),
        _release("0000019617-26-000001", JPM, "complete", not_applicable_overrides=[OVERRIDE]),
        _release(
            "0000034088-26-000001", EXXON, "failed", notes=["ex991.htm: no tables, 3 pictures"]
        ),
        _release("0002115436-26-000001", XOM, "no_statement", notes=["ex991.htm: no tables"]),
    ]
    facts = [
        *_facts("0000320193-26-000001", "passed", "passed", "passed", "passed"),
        *_facts("0000320193-26-000002", "passed", "failed", "passed", "not_found"),
        *_facts("0000019617-26-000001", "not_applicable", "not_applicable", "passed", "passed"),
        *_facts("0000034088-26-000001", "not_found", "not_found", "not_found", "not_found"),
        *_facts("0002115436-26-000001", "not_found", "not_found", "not_found", "not_found"),
    ]
    return tally(
        releases,
        facts,
        names={APPLE: "Apple Inc.", JPM: "JPMorgan Chase", XOM: "ExxonMobil", EXXON: "Exxon"},
        tickers={APPLE: "AAPL", JPM: "JPM", XOM: "XOM"},
        successors={EXXON: XOM},
    )


def test_slots_are_counted_per_slot_and_in_total() -> None:
    report = _report()

    assert report.releases == 5
    assert report.statuses == {"complete": 2, "partial": 1, "failed": 1, "no_statement": 1}
    assert report.slots["operating_income"] == SlotCounts(
        passed=1, failed=1, not_found=1, not_applicable=1, overridden=1
    )
    # The filing without a statement isn't counted: 8 of the 14 slots that apply passed.
    assert (report.total.passed, report.total.applicable) == (8, 14)
    assert report.total.hit_rate == Decimal("57.1")
    assert report.failures == {"change_over_1000_pct": 1}
    assert report.overrides == {OVERRIDE: 1}


def test_companies_are_counted_by_filer_and_a_predecessor_shows_its_successor() -> None:
    companies = {company.cik: company for company in _report().companies}

    assert companies[APPLE].slots == SlotCounts(passed=6, failed=1, not_found=1)
    assert companies[APPLE].slots.hit_rate == Decimal("75.0")
    assert companies[JPM].slots == SlotCounts(passed=2, not_applicable=2, overridden=1)
    exxon = companies[EXXON]
    assert (exxon.ticker, exxon.successor_cik, exxon.releases) == ("XOM", XOM, 1)
    assert (companies[XOM].releases, companies[XOM].no_statement) == (0, 1)


def test_filings_without_a_statement_are_listed_for_a_check_by_hand() -> None:
    [filing] = _report().no_statement

    assert (filing.accession_no, filing.ticker, filing.notes) == (
        "0002115436-26-000001",
        "XOM",
        ("ex991.htm: no tables",),
    )


def test_the_report_prints_slots_overrides_and_companies() -> None:
    lines = _report_lines(_report())

    assert lines[:2] == [
        "Earnings releases: 5 (complete 2, partial 1, failed 1, no statement 1); rules version 2",
        "Hit rate: 8 of the 14 slots that apply passed (57.1%) in the 4 releases with a statement",
    ]
    assert "  operating_income         1       1          1     1          1     33.3%" in lines
    assert f"     1  {OVERRIDE}" in lines
    assert any(line.startswith("  XOM     34088    predecessor") for line in lines)


def test_no_slot_that_applies_has_no_hit_rate() -> None:
    assert SlotCounts(not_applicable=4).hit_rate is None
