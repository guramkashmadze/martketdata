"""E1-S5: the committed SEC fixtures are present, unchanged since they were fetched, small,
and cover a September, a December and a January fiscal year end."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from marketdata.sec import urls

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec"
MANIFEST = json.loads((FIXTURES / "MANIFEST.json").read_text())

# Ticker, CIK, and the month the latest 10-K's fiscal year ends in.
COMPANIES = [("AAPL", 320193, "09"), ("GOOGL", 1652044, "12"), ("TGT", 27419, "01")]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_every_fixture_is_in_the_manifest() -> None:
    on_disk = {
        p.relative_to(FIXTURES).as_posix()
        for p in FIXTURES.rglob("*.json")
        if p.name != "MANIFEST.json"
    }
    assert on_disk == set(MANIFEST)


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_fixture_matches_its_manifest_entry(name: str) -> None:
    entry = MANIFEST[name]
    data = (FIXTURES / name).read_bytes()

    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert len(data) == entry["size_bytes"]
    assert urls.parse(entry["url"]) is not None
    if entry["trim"] is None:
        assert entry["sha256"] == entry["original_sha256"]


def test_fixture_folder_stays_small() -> None:
    total = sum(p.stat().st_size for p in FIXTURES.rglob("*") if p.is_file())
    assert total < 20 * 1024 * 1024  # PLAN.md §6, all epics together
    e1 = sum(entry["size_bytes"] for entry in MANIFEST.values())
    assert e1 < 3 * 1024 * 1024  # E1's share


@pytest.mark.parametrize(("ticker", "cik", "fy_end_month"), COMPANIES)
def test_submissions_cover_the_three_fiscal_year_ends(
    ticker: str, cik: int, fy_end_month: str
) -> None:
    submissions = load(f"submissions/{ticker}.json")
    recent = submissions["filings"]["recent"]
    rows = [dict(zip(recent, values, strict=True)) for values in zip(*recent.values(), strict=True)]
    latest_10k = next(row for row in rows if row["form"] == "10-K")

    assert MANIFEST[f"submissions/{ticker}.json"]["url"] == urls.submissions(cik)
    assert ticker in submissions["tickers"]
    assert latest_10k["reportDate"][5:7] == fy_end_month
    assert len({len(column) for column in recent.values()}) == 1  # columnar arrays align


@pytest.mark.parametrize(("ticker", "cik", "fy_end_month"), COMPANIES)
def test_cik_types_differ_between_endpoints(ticker: str, cik: int, fy_end_month: str) -> None:
    """Edge case: submissions gives the CIK as a zero-padded string, companyfacts as an int."""
    assert load(f"submissions/{ticker}.json")["cik"] == urls.cik10(cik)
    assert load(f"companyfacts/{ticker}.json")["cik"] == cik


@pytest.mark.parametrize(("ticker", "cik", "fy_end_month"), COMPANIES)
def test_companyfacts_keep_the_core_concepts(ticker: str, cik: int, fy_end_month: str) -> None:
    facts = load(f"companyfacts/{ticker}.json")["facts"]

    assert MANIFEST[f"companyfacts/{ticker}.json"]["url"] == urls.companyfacts(cik)
    assert set(facts) == {"us-gaap", "dei"}
    for concept in ("NetIncomeLoss", "EarningsPerShareDiluted", "Assets", "StockholdersEquity"):
        assert concept in facts["us-gaap"], concept
    assert "EntityPublicFloat" in facts["dei"]
    assert "USD/shares" in facts["us-gaap"]["EarningsPerShareDiluted"]["units"]


def test_multi_class_company_has_no_dei_shares_outstanding() -> None:
    """Edge case: Alphabet (several share classes) has no dei:EntityCommonStockSharesOutstanding
    in companyfacts; the §9.3 fallback us-gaap:CommonStockSharesOutstanding is there."""
    googl = load("companyfacts/GOOGL.json")["facts"]
    aapl = load("companyfacts/AAPL.json")["facts"]

    assert "EntityCommonStockSharesOutstanding" not in googl["dei"]
    assert googl["us-gaap"]["CommonStockSharesOutstanding"]["units"]["shares"]
    assert "EntityCommonStockSharesOutstanding" in aapl["dei"]


def test_fiscal_year_end_field_is_not_the_latest_reported_year_end() -> None:
    """Edge case: Target's submissions say fiscalYearEnd 0201; its latest 10-K ends 2026-01-31."""
    submissions = load("submissions/TGT.json")
    recent = submissions["filings"]["recent"]
    latest_10k = recent["reportDate"][recent["form"].index("10-K")]

    assert submissions["fiscalYearEnd"] == "0201"
    assert latest_10k == "2026-01-31"
