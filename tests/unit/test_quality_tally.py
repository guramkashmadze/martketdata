"""E4b-S3, S6: the quality report's tally: flags by company and metric, stale companies, a
predecessor's rows under its own CIK with the ticker of the company it became, and the
release-vs-filing mismatches."""

from datetime import date
from decimal import Decimal

from marketdata.db.repositories.earnings import CrossChecked
from marketdata.service.quality import tally

XOM, EXXON = 2115436, 34088
TODAY = date(2026, 10, 29)


def test_a_predecessor_is_listed_under_its_own_cik_and_never_stale_alone() -> None:
    counts = [
        (XOM, "revenue", "ttm_gap", 1, date(2026, 6, 30)),
        (EXXON, "eps_diluted", "eps_q4_not_reported", 4, date(2025, 12, 31)),
        (EXXON, "revenue", "ttm_gap", 3, date(2024, 12, 31)),
    ]
    rows = {XOM: (100, 1, date(2026, 8, 4)), EXXON: (900, 7, date(2026, 5, 1))}

    report = tally(
        counts,
        rows,
        today=TODAY,
        names={XOM: "ExxonMobil Holdings Corp", EXXON: "EXXON MOBIL CORP"},
        tickers={XOM: "XOM"},
        successors={EXXON: XOM},
    )

    company, predecessor = report.companies
    assert (company.cik, company.ticker, company.stale) == (XOM, "XOM", False)
    assert (predecessor.cik, predecessor.ticker, predecessor.successor_cik) == (EXXON, "XOM", XOM)
    # The group's latest filing is the company's: the predecessor isn't stale on its own.
    assert (predecessor.latest_filed, predecessor.stale) == (date(2026, 8, 4), False)
    assert report.by_flag == {"eps_q4_not_reported": 4, "ttm_gap": 4}
    assert (report.rows, report.flagged) == (1000, 8)


def test_a_company_whose_filings_stopped_is_stale_and_flags_can_be_chosen() -> None:
    counts = [
        (1, "revenue", "outlier_jump", 2, date(2026, 3, 31)),
        (1, "cash", "cash_includes_restricted", 9, date(2026, 3, 31)),
    ]

    report = tally(
        counts,
        {1: (50, 11, date(2026, 5, 1))},
        today=TODAY,
        names={1: "Old Co"},
        tickers={},
        successors={},
        flags=["outlier_jump"],
    )

    [company] = report.stale
    assert [entry.flag for entry in company.flags] == ["outlier_jump"]
    assert report.by_flag == {"outlier_jump": 2}


def test_release_mismatches_appear_with_both_values_and_the_match_rate() -> None:
    def slot(name: str, status: str, value: str, filing: str | None) -> CrossChecked:
        return CrossChecked(
            cik=1874178,
            accession_no="0001874178-26-000053",
            period_end=date(2026, 6, 30),
            slot=name,
            unit="USD",
            value=Decimal(value),
            cross_check_status=status,
            cross_check_value=Decimal(filing) if filing else None,
            cross_check_accession="0001874178-26-000054" if filing else None,
        )

    checked = [
        slot("revenue", "mismatch", "3039", "1658"),
        slot("operating_income", "matched", "-836", "-836"),
        slot("net_income", "matched", "-833", "-833"),
        slot("eps_diluted", "not_comparable", "1", None),
    ]

    report = tally(
        [], {}, today=TODAY, names={}, tickers={1874178: "RIVN"}, successors={}, checked=checked
    )

    checks = report.cross_checks
    assert checks.statuses == {"matched": 2, "mismatch": 1, "not_comparable": 1, "pending": 0}
    assert checks.match_rate == Decimal("66.7")
    [mismatch] = checks.mismatches
    assert (mismatch.ticker, mismatch.slot, mismatch.difference_pct) == (
        "RIVN",
        "revenue",
        Decimal("83.3"),
    )
