"""E2-S1: the committed data files parse, and every override points at an SEC filing page.

Whether each universe ticker is listed today is checked by `marketdata ingest security-master`
against the live SEC lists; this test only needs the files themselves.
"""

from pathlib import Path

from marketdata.normalize.security_master import (
    build_snapshot,
    parse_exchange_list,
    parse_fund_list,
)
from marketdata.normalize.universe import parse_security_overrides, parse_universe
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
