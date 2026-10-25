"""E2-S2: submissions JSON gives a company's details, and its newest periodic report decides
its class (PLAN.md §9.1, D-052). Fixtures are whole SEC files from 2026-10-24 and 2026-10-25."""

import json
from datetime import date
from pathlib import Path

import pytest

from marketdata.normalize.security_master import (
    CompanyDetails,
    Filing,
    SecFileError,
    classify_filer,
    parse_submissions,
)

SUBMISSIONS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "submissions"


def details(ticker: str) -> CompanyDetails:
    return parse_submissions((SUBMISSIONS / f"{ticker}.json").read_bytes())


@pytest.mark.parametrize(
    ("ticker", "filer_type", "reason"),
    [
        ("AAPL", "domestic", "10-Q filed 2026-07-31"),
        ("GOOGL", "domestic", "10-Q filed 2026-07-23"),
        ("TGT", "domestic", "10-Q filed 2026-08-28"),
        # Filed 40-F and 6-K until 2024, 10-K and 10-Q since 2025: the newest report decides.
        ("SHOP", "domestic", "10-Q filed 2026-08-05"),
        # Listed in 2026: 10-Qs, no 10-K yet.
        ("SPCX", "domestic", "10-Q filed 2026-10-22"),
        (
            "GLD",
            "fund",
            "10-Q filed 2026-08-04; SIC 6221 and no 8-K item 2.02: an exchange-traded commodity "
            "trust or pool",
        ),
        # A unit investment trust: in the exchange list, not the fund list.
        ("SPY", "fund", "NPORT-P filed 2026-08-28"),
        ("TSM", "foreign_private_issuer", "20-F filed 2026-04-16"),
        ("BABA", "foreign_private_issuer", "20-F filed 2026-05-20"),
    ],
)
def test_class_from_the_newest_periodic_report(ticker: str, filer_type: str, reason: str) -> None:
    classification = classify_filer(details(ticker), in_fund_list=False)

    assert (classification.filer_type, classification.reason) == (filer_type, reason)


def test_the_fund_list_decides_before_the_filings() -> None:
    classification = classify_filer(details("VOO"), in_fund_list=True)

    assert (classification.filer_type, classification.reason) == (
        "fund",
        "listed in company_tickers_mf.json",
    )


def test_without_submissions_the_class_is_unknown() -> None:
    assert classify_filer(None, in_fund_list=False).filer_type == "unknown"
    assert classify_filer(None, in_fund_list=True).filer_type == "fund"


def synthetic(*filings: tuple[str, str, str], sic: str | None = None) -> CompanyDetails:
    rows = tuple(
        Filing(form, date.fromisoformat(filed), tuple(items.split(",")) if items else ())
        for form, filed, items in filings
    )
    return CompanyDetails(1, "Example", sic, None, None, None, rows)


@pytest.mark.parametrize(
    ("filings", "sic", "filer_type"),
    [
        ((("40-F", "2025-12-03", ""), ("6-K", "2026-08-27", "")), None, "foreign_private_issuer"),
        ((("6-K", "2026-08-27", ""),), None, "foreign_private_issuer"),
        ((("8-K", "2026-08-27", "8.01"), ("S-1", "2026-05-01", "")), None, "other"),
        ((), None, "other"),
        # A 10-K filer with SIC 6221 that reports results is an operating company.
        ((("10-Q", "2026-08-04", ""), ("8-K", "2026-07-30", "2.02,9.01")), "6221", "domestic"),
        ((("10-Q", "2026-08-04", ""),), "6221", "fund"),
        ((("10-K/A", "2026-03-01", ""), ("20-F", "2025-04-01", "")), None, "domestic"),
    ],
)
def test_synthetic_filing_histories(
    filings: tuple[tuple[str, str, str], ...], sic: str | None, filer_type: str
) -> None:
    assert classify_filer(synthetic(*filings, sic=sic), in_fund_list=False).filer_type == filer_type


def test_details_as_the_sec_gives_them() -> None:
    target = details("TGT")

    assert (target.cik, target.name) == (27419, "TARGET CORP")
    assert (target.sic_code, target.sic_description) == ("5331", "Retail-Variety Stores")
    # fiscalYearEnd is the current year's end, not the latest reported one (EDGE_CASES.md).
    assert (target.fiscal_year_end, target.state_of_inc) == ("0201", "MN")


def test_empty_and_missing_values_become_none() -> None:
    spy, gld, baba = details("SPY"), details("GLD"), details("BABA")

    assert (spy.sic_code, spy.sic_description, spy.state_of_inc) == (None, None, "NY")
    assert gld.state_of_inc is None  # '' in the file
    assert baba.fiscal_year_end is None  # null in the file
    assert baba.state_of_inc == "K3"  # Hong Kong, as EDGAR codes it


def test_recent_filings_newest_first_with_8k_items() -> None:
    apple = details("AAPL")
    raw = json.loads((SUBMISSIONS / "AAPL.json").read_bytes())["filings"]["recent"]

    assert len(apple.filings) == len(raw["form"])
    assert [f.filed for f in apple.filings] == sorted(
        (f.filed for f in apple.filings), reverse=True
    )
    results = [f for f in apple.filings if f.form == "8-K" and "2.02" in f.items]
    assert results and results[0].items == ("2.02", "9.01")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"{", "not JSON"),
        (b"[]", "expected an object"),
        (b'{"cik": 320193, "name": "Apple Inc."}', "no usable cik and name"),
        (b'{"cik": "0000320193", "name": " "}', "no usable cik and name"),
        (b'{"cik": "0000320193", "name": "Apple Inc.", "filings": {}}', "no filings.recent"),
        (
            b'{"cik": "0000320193", "name": "A", "filings": {"recent": {"form": ["8-K"], '
            b'"filingDate": []}}}',
            "don't align",
        ),
        (
            b'{"cik": "0000320193", "name": "A", "filings": {"recent": {"form": ["8-K"], '
            b'"filingDate": ["2026-13-01"]}}}',
            "bad filingDate",
        ),
    ],
)
def test_malformed_submissions_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(SecFileError, match=message):
        parse_submissions(data)
