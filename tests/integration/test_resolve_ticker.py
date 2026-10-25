"""E2-S3: `resolve_ticker` gives the current security, its class and CIK for any SEC-listed
ticker, written the way people write it (PLAN.md §9.2), and NotFound otherwise.

resolve_ticker reads PostgreSQL, so these tests seed it with a security-master load from the
fixtures; the spelling rules themselves are unit-tested in tests/unit/test_tickers.py.
"""

from datetime import date
from typing import Any

import psycopg
import pytest
from conftest import AS_OF, SEC_FIXTURES, LoadMaster, fixture_routes

from marketdata.sec import urls
from marketdata.service.companies import resolve_ticker
from marketdata.service.errors import InvalidParameter, NotFound

BEFORE = date(2026, 6, 30)


@pytest.fixture
async def seeded(conn: psycopg.AsyncConnection[Any], load_master: LoadMaster) -> None:
    """The crafted list of 2026-06-30, then today's: XOM moved, DLST went away."""
    routes = fixture_routes()
    routes[urls.COMPANY_TICKERS_EXCHANGE] = (
        SEC_FIXTURES / "tickers/company_tickers_exchange_before.json"
    ).read_bytes()
    await load_master(routes, as_of=BEFORE)
    await load_master(as_of=AS_OF)


@pytest.mark.parametrize("text", ["AAPL", "aapl", " Aapl "])
async def test_case_and_spaces_do_not_matter(
    conn: psycopg.AsyncConnection[Any], seeded: None, text: str
) -> None:
    security = await resolve_ticker(conn, text)

    assert (security.ticker, security.cik, security.name) == ("AAPL", 320193, "Apple Inc.")
    assert (security.filer_type, security.in_universe, security.security_type) == (
        "domestic",
        True,
        "common",
    )
    assert security.exchange == "Nasdaq"


@pytest.mark.parametrize("text", ["brk.b", "BRK-B", "BRK/B", "brk-b"])
async def test_separators_are_the_same(
    conn: psycopg.AsyncConnection[Any], seeded: None, text: str
) -> None:
    security = await resolve_ticker(conn, text)

    assert (security.ticker, security.cik, security.share_class) == ("BRK-B", 1067983, "B")


async def test_two_share_classes_of_one_company(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    googl, goog = await resolve_ticker(conn, "GOOGL"), await resolve_ticker(conn, "GOOG")
    brk_a, brk_b = await resolve_ticker(conn, "BRK.A"), await resolve_ticker(conn, "BRK.B")

    assert googl.cik == goog.cik == 1652044 and googl.ticker != goog.ticker
    assert brk_a.cik == brk_b.cik == 1067983
    assert (brk_a.share_class, brk_b.share_class) == ("A", "B")


async def test_a_closed_ticker_is_not_found_and_says_what_it_was(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    with pytest.raises(NotFound) as excinfo:
        await resolve_ticker(conn, "dlst")

    assert str(excinfo.value) == (
        "DLST is no longer listed: it was the ticker of CIK 9999999999 (TEST DELISTED CO) "
        "until 2026-10-25."
    )


async def test_a_ticker_that_moved_gives_its_current_company(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    security = await resolve_ticker(conn, "XOM")

    assert (security.cik, security.name, security.listed_since) == (
        2115436,
        "ExxonMobil Holdings Corp",
        AS_OF,
    )


async def test_a_reused_ticker_gives_its_current_owner(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    # FB was Meta's ticker (closed here by hand); the SEC fund list now gives it to a
    # ProShares Trust series.
    await conn.execute(
        "insert into securities (cik, ticker, valid_from, valid_to)"
        " values (1326801, 'FB', '2012-05-18', '2022-06-09')"
    )

    security = await resolve_ticker(conn, "fb")

    assert (security.cik, security.filer_type, security.security_type) == (1174610, "fund", "fund")
    assert security.name is None  # the fund list has no names, and it isn't in the universe


async def test_an_etf(conn: psycopg.AsyncConnection[Any], seeded: None) -> None:
    voo = await resolve_ticker(conn, "voo")
    spy = await resolve_ticker(conn, "SPY")

    assert (voo.cik, voo.name, voo.filer_type, voo.security_type) == (
        36405,
        "VANGUARD INDEX FUNDS",
        "fund",
        "fund",
    )
    assert voo.exchange is None  # the fund list has no exchanges
    # SPY is in the exchange list, as a trust: a fund by its filings (D-052).
    assert (spy.filer_type, spy.security_type, spy.exchange) == ("fund", "common", "NYSE")


async def test_a_foreign_issuer(conn: psycopg.AsyncConnection[Any], seeded: None) -> None:
    tsm, otc = await resolve_ticker(conn, "TSM"), await resolve_ticker(conn, "tsmwf")

    assert (tsm.cik, tsm.filer_type, tsm.exchange) == (1046179, "foreign_private_issuer", "NYSE")
    assert (otc.cik, otc.exchange) == (1046179, "OTC")


async def test_a_listed_company_outside_the_universe_resolves_unclassified(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    jpm, preferred = await resolve_ticker(conn, "JPM"), await resolve_ticker(conn, "jpm.pc")

    assert (jpm.name, jpm.filer_type, jpm.in_universe) == ("JPMORGAN CHASE & CO", "unknown", False)
    assert (preferred.ticker, preferred.security_type) == ("JPM-PC", "preferred")


async def test_unknown_and_invalid_tickers(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    with pytest.raises(NotFound, match="No security has the ticker ZZZZ in the SEC's lists"):
        await resolve_ticker(conn, "zzzz")
    for text in ("", "BRK B", "AAPL;"):
        with pytest.raises(InvalidParameter, match="is not a ticker"):
            await resolve_ticker(conn, text)
