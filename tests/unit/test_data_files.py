"""E2-S1: the committed data files parse, and every override points at an SEC filing page.

Whether each universe ticker is listed today is checked by `marketdata ingest security-master`
against the live SEC lists; this test only needs the files themselves.
"""

from pathlib import Path

import pytest

from marketdata.extract.earnings_rules import OverrideError
from marketdata.ingest.earnings import read_overrides
from marketdata.ingest.f13 import read_unit_overrides
from marketdata.normalize.holdings import UnitOverrideError
from marketdata.normalize.security_master import (
    build_snapshot,
    parse_exchange_list,
    parse_fund_list,
)
from marketdata.normalize.universe import (
    parse_managers,
    parse_predecessors,
    parse_security_overrides,
    parse_universe,
)
from marketdata.sec import urls

ROOT = Path(__file__).resolve().parents[2]
TICKERS = ROOT / "tests" / "fixtures" / "sec" / "tickers"


def test_universe_file_parses() -> None:
    entries = parse_universe((ROOT / "data" / "universe.csv").read_text())

    # D-050: 31 domestic filers, 4 ETFs and 4 foreign private issuers; D-056: 35 more on request.
    assert len(entries) == 74
    assert all(len(entry.note) <= 140 for entry in entries)  # a one-line note


# The company that lists each overridden ticker. Whether a list still has the ticker is checked
# by every live load ("Override for ... unused").
OVERRIDE_CIKS = {
    "GOOGM": 1652044,
    "GOOGN": 1652044,
    "AMJB": 19617,
    "VYLD": 19617,
    "AUROW": 1828108,
}


def test_overrides_point_at_a_filing_page_of_the_listing_company() -> None:
    overrides = parse_security_overrides((ROOT / "data" / "security_overrides.csv").read_text())

    assert {o.ticker: OVERRIDE_CIKS.get(o.ticker) for o in overrides} == OVERRIDE_CIKS
    for override in overrides:
        parsed = urls.parse(override.source_url)
        assert parsed is not None and parsed.endpoint == "filing", override
        assert parsed.cik == OVERRIDE_CIKS[override.ticker], override


def test_overrides_apply_to_the_fixture_lists() -> None:
    overrides = parse_security_overrides((ROOT / "data" / "security_overrides.csv").read_text())
    snapshot = build_snapshot(
        parse_exchange_list((TICKERS / "company_tickers_exchange.json").read_bytes()),
        parse_fund_list((TICKERS / "company_tickers_mf.json").read_bytes()),
        overrides,
    )

    for ticker in ("GOOGM", "GOOGN"):
        assert snapshot.listings[ticker].security_type == "preferred"
    for ticker in ("AMJB", "VYLD"):
        assert snapshot.listings[ticker].security_type == "other"


def test_predecessors_point_at_a_filing_of_the_company_now() -> None:
    links = parse_predecessors((ROOT / "data" / "predecessors.csv").read_text())

    assert [(link.cik, link.predecessor_cik) for link in links] == [(2115436, 34088)]  # XOM
    for link in links:
        parsed = urls.parse(link.source_url)
        assert parsed is not None and parsed.endpoint == "filing"
        assert parsed.cik == link.cik  # the successor's 8-K12B


def test_the_managers_file_lists_20_to_30_managers_each_with_what_edgar_showed() -> None:
    entries = parse_managers((ROOT / "data" / "managers.csv").read_text())

    assert 20 <= len(entries) <= 30  # E9-S1
    assert len({entry.manager_cik for entry in entries}) == len(entries)
    assert all(entry.notes.startswith("EDGAR, 2026-") for entry in entries)
    by_cik = {entry.manager_cik: entry.display_name for entry in entries}
    assert by_cik[1067983] == "Berkshire Hathaway"  # also the universe company BRK-B
    assert by_cik[2026053] == "Pershing Square"  # not 1336528, which files only a 13F-NT now
    assert 1649339 not in by_cik  # Scion: no 13F since its Q3 2025 report


def test_the_13f_unit_overrides_cite_a_filing_of_each_filer() -> None:
    overrides = read_unit_overrides(ROOT / "data" / "f13_overrides.toml")

    # E9-S2: Baupost and Duquesne Family Office kept reporting thousands after 2023-01-03.
    assert [(o.manager_cik, o.filed_from.isoformat(), o.unit) for o in overrides] == [
        (1061768, "2023-01-03", "thousands"),
        (1536411, "2023-05-15", "thousands"),
    ]
    managers = {
        entry.manager_cik for entry in parse_managers((ROOT / "data" / "managers.csv").read_text())
    }
    assert {override.manager_cik for override in overrides} <= managers


def test_a_unit_override_citing_another_filers_page_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "f13_overrides.toml"
    path.write_text(
        '[[unit]]\nmanager_cik = 1061768\nfiled_from = 2023-01-03\nunit = "thousands"\n'
        'reason = "r"\nsource_url = "https://www.sec.gov/Archives/edgar/data/1067983/'
        '000119312526352200/0001193125-26-352200-index.htm"\n'
    )

    with pytest.raises(UnitOverrideError, match="not a filing page of the filer"):
        read_unit_overrides(path)
    assert read_unit_overrides(tmp_path / "missing.toml") == ()


def test_the_extraction_overrides_file_parses_and_cites_sec_filings() -> None:
    overrides = read_overrides(ROOT / "data" / "extraction_overrides.toml")

    for override in overrides:
        parsed = urls.parse(override.source_url)
        assert parsed is not None and parsed.endpoint == "filing"
        assert parsed.cik == override.cik or override.cik in OVERRIDE_PREDECESSORS


# Overrides may cite the successor's filings for a predecessor's history (D-068).
OVERRIDE_PREDECESSORS = {34088}


def test_an_override_citing_another_site_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "overrides.toml"
    path.write_text(
        '[[override]]\ncik = 1\nslot = "revenue"\nrow_label = "Sales"\nreason = "r"\n'
        'source_url = "https://example.com/release.htm"\n'
    )

    with pytest.raises(OverrideError, match="not an SEC filing page"):
        read_overrides(path)
