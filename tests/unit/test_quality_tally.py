"""E4b-S3: the quality report's tally: flags by company and metric, stale companies, and a
predecessor's rows under its own CIK with the ticker of the company it became."""

from datetime import date

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
