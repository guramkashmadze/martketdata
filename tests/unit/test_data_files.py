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

    assert len(entries) == 39  # D-050: 31 domestic filers, 4 ETFs, 4 foreign private issuers
    assert all(len(entry.note) <= 140 for entry in entries)  # a one-line note


def test_overrides_point_at_sec_filing_pages_of_listed_tickers() -> None:
    overrides = parse_security_overrides((ROOT / "data" / "security_overrides.csv").read_text())
    snapshot = build_snapshot(
        parse_exchange_list((TICKERS / "company_tickers_exchange.json").read_bytes()),
        parse_fund_list((TICKERS / "company_tickers_mf.json").read_bytes()),
        overrides,
    )

    assert {o.ticker for o in overrides} == {"GOOGM", "GOOGN", "AMJB", "VYLD"}
    for override in overrides:
        parsed = urls.parse(override.source_url)
        assert parsed is not None and parsed.endpoint == "filing", override
        assert parsed.cik == snapshot.listings[override.ticker].cik
    assert snapshot.unused_overrides == ()
