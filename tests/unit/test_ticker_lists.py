"""E2-S1/S2: the SEC ticker lists become one snapshot of current tickers (PLAN.md §9.2).

Fixtures: both lists as fetched on 2026-10-25, trimmed to a few companies (MANIFEST.json).
"""

import json
from pathlib import Path

import pytest

from marketdata.normalize.security_master import (
    SecFileError,
    TickerSnapshot,
    build_snapshot,
    parse_exchange_list,
    parse_fund_list,
)
from marketdata.normalize.universe import SecurityOverride, UniverseEntry, match_universe

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "tickers"
EXCHANGE = (FIXTURES / "company_tickers_exchange.json").read_bytes()
FUND = (FIXTURES / "company_tickers_mf.json").read_bytes()
GOOGM = SecurityOverride(2, "GOOGM", "preferred", "https://www.sec.gov/R1.htm")


@pytest.fixture(scope="module")
def snapshot() -> TickerSnapshot:
    return build_snapshot(parse_exchange_list(EXCHANGE), parse_fund_list(FUND), [GOOGM])


def test_lists_parse_with_their_fields() -> None:
    exchange = parse_exchange_list(EXCHANGE)
    fund = parse_fund_list(FUND)

    assert len(exchange) == 51 and len(fund) == 447
    apple = next(row for row in exchange if row.ticker == "AAPL")
    assert (apple.cik, apple.name, apple.exchange) == (320193, "Apple Inc.", "Nasdaq")
    voo = next(row for row in fund if row.symbol == "VOO")
    assert (voo.cik, voo.series_id, voo.class_id) == (36405, "S000002839", "C000092055")


def test_snapshot_keeps_one_listing_per_ticker(snapshot: TickerSnapshot) -> None:
    # 51 + 447 rows, less NONE., the empty fund symbol and the fund list's SPCX.
    assert len(snapshot.listings) == 51 + 447 - 3
    assert (snapshot.exchange_rows, snapshot.fund_rows) == (51, 447)


def test_share_classes_preferreds_and_other_instruments(snapshot: TickerSnapshot) -> None:
    listings = snapshot.listings
    assert (listings["BRK-A"].security_type, listings["BRK-A"].share_class) == ("common", "A")
    assert (listings["BRK-B"].cik, listings["GOOG"].cik) == (1067983, 1652044)
    assert listings["JPM-PC"].security_type == "preferred"
    assert listings["VAL-WT"].security_type == "other"
    assert listings["VOO"].security_type == "fund"
    assert listings["VOO"].exchange is None and listings["VOO"].source == "fund"


def test_override_corrects_a_plain_ticker(snapshot: TickerSnapshot) -> None:
    assert snapshot.listings["GOOGM"].security_type == "preferred"
    assert snapshot.listings["GOOGN"].security_type == "common"  # no override given here
    assert snapshot.unused_overrides == ()


def test_unused_override_is_reported() -> None:
    gone = SecurityOverride(3, "GONE", "other", "https://www.sec.gov/R1.htm")

    snapshot = build_snapshot(parse_exchange_list(EXCHANGE), parse_fund_list(FUND), [gone])

    assert snapshot.unused_overrides == (gone,)


def test_entries_that_are_not_tickers_are_skipped_and_reported(snapshot: TickerSnapshot) -> None:
    assert [(s.source, s.cik, s.text, s.reason) for s in snapshot.skipped] == [
        ("exchange", 1347123, "NONE.", "not a ticker"),
        ("fund", 2078265, "", "empty symbol"),
    ]
    # Fund-list spellings are made canonical, not skipped.
    assert snapshot.listings["ELFNX"].cik == 32339
    assert snapshot.listings["NWAKX"].cik == 1048702


def test_ticker_in_both_lists_keeps_the_exchange_listing(snapshot: TickerSnapshot) -> None:
    (collision,) = snapshot.collisions

    assert collision.ticker == "SPCX"
    assert (collision.kept.cik, collision.kept.source) == (1181412, "exchange")
    assert (collision.dropped_cik, collision.dropped_source) == (1719812, "fund")
    assert snapshot.listings["SPCX"].cik == 1181412


def test_companies_carry_exchange_names_and_the_fund_flag(snapshot: TickerSnapshot) -> None:
    companies = snapshot.companies
    assert companies[27419].name == "TARGET CORP" and not companies[27419].in_fund_list
    assert companies[36405].name is None and companies[36405].in_fund_list
    # A company whose only entry was skipped is still a listed company.
    assert companies[1347123].name == "EBR Systems, Inc."
    assert len(companies) == 25 + 7


def test_a_fund_with_an_exchange_listing_becomes_a_fund_security() -> None:
    exchange = parse_exchange_list(
        b'{"fields":["cik","name","ticker","exchange"],"data":[[884394,"SPDR TRUST","SPY","NYSE"]]}'
    )
    fund = parse_fund_list(
        b'{"fields":["cik","seriesId","classId","symbol"],"data":[[884394,"S1","C1","SPY"]]}'
    )

    snapshot = build_snapshot(exchange, fund)

    spy = snapshot.listings["SPY"]
    assert (spy.security_type, spy.exchange, spy.source) == ("fund", "NYSE", "exchange")
    assert snapshot.collisions == ()
    assert snapshot.companies[884394].in_fund_list


def test_universe_tickers_are_validated_against_the_lists(snapshot: TickerSnapshot) -> None:
    entries = [
        UniverseEntry(2, "AAPL", "n"),
        UniverseEntry(3, "VOO", "n"),
        UniverseEntry(4, "SPCX", "n"),
        UniverseEntry(5, "NOPE", "n"),
    ]
    listed = {ticker: listing.cik for ticker, listing in snapshot.listings.items()}

    match = match_universe(entries, listed)

    assert [(e.ticker, cik) for e, cik in match.matched] == [
        ("AAPL", 320193),
        ("VOO", 36405),
        ("SPCX", 1181412),
    ]
    assert [e.ticker for e in match.unknown] == ["NOPE"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not json", "is not JSON"),
        (b"[]", "expected an object"),
        (b'{"fields":["cik","name","ticker"],"data":[[1,"a","B"]]}', "has no field exchange"),
        (b'{"fields":["cik","name","ticker","exchange"],"data":[]}', "lists no rows"),
        (b'{"fields":["cik","name","ticker","exchange"],"data":[[1,"a"]]}', "does not match"),
        (b'{"fields":["cik","name","ticker","exchange"],"data":[["1","a","B",null]]}', "malformed"),
        (
            b'{"fields":["cik","name","ticker","exchange"],"data":[[true,"a","B",null]]}',
            "malformed",
        ),
    ],
)
def test_a_list_in_an_unexpected_shape_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(SecFileError, match=message):
        parse_exchange_list(data)


def test_fixtures_are_the_sec_layout() -> None:
    exchange = json.loads(EXCHANGE)
    assert exchange["fields"] == ["cik", "name", "ticker", "exchange"]
    assert json.loads(FUND)["fields"] == ["cik", "seriesId", "classId", "symbol"]
