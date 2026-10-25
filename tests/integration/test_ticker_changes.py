"""E2-S4: the daily run closes rows for tickers that went away or moved to another CIK and
opens new ones, so history stays; tested with two snapshot fixtures (D-054).

`company_tickers_exchange_before.json` is crafted to look like the list before 2026-07-01
(XOM on Exxon Mobil Corp's CIK 34088, no SpaceX, one more ticker, DLST); the other fixture is
the list of 2026-10-25.
"""

from datetime import date
from typing import Any

import psycopg
import pytest
from conftest import AS_OF, SEC_FIXTURES, TEST_UNIVERSE_CSV, LoadMaster, fixture_routes

from marketdata.db.repositories import companies
from marketdata.normalize.security_master import SnapshotRefused
from marketdata.sec import urls

BEFORE = date(2026, 6, 30)


def before_routes() -> dict[str, bytes | int]:
    routes = fixture_routes()
    routes[urls.COMPANY_TICKERS_EXCHANGE] = (
        SEC_FIXTURES / "tickers/company_tickers_exchange_before.json"
    ).read_bytes()
    return routes


async def history(conn: psycopg.AsyncConnection[Any], ticker: str) -> list[tuple[Any, ...]]:
    cur = await conn.execute(
        "select cik, valid_from, valid_to from securities where ticker = %s order by valid_from",
        [ticker],
    )
    return await cur.fetchall()


async def all_rows(conn: psycopg.AsyncConnection[Any]) -> list[tuple[Any, ...]]:
    cur = await conn.execute("select ctid::text, * from securities order by security_id")
    return await cur.fetchall()


async def test_moved_and_gone_tickers_are_closed_and_new_ones_opened(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master(before_routes(), as_of=BEFORE)

    report = await load_master(as_of=AS_OF)

    assert await history(conn, "XOM") == [(34088, BEFORE, AS_OF), (2115436, AS_OF, None)]
    assert await history(conn, "SPCX") == [(1719812, BEFORE, AS_OF), (1181412, AS_OF, None)]
    assert await history(conn, "DLST") == [(9999999999, BEFORE, AS_OF)]
    assert await history(conn, "AAPL") == [(320193, BEFORE, None)]
    changes = report.securities
    assert (len(changes.opened), len(changes.closed), len(changes.moved)) == (2, 3, 2)
    lines = report.lines("universe.csv")
    assert (
        "  Moved to another CIK (2): SPCX from CIK 1719812 to CIK 1181412; "
        "XOM from CIK 34088 to CIK 2115436" in lines
    )
    assert "  No longer listed (1): DLST (CIK 9999999999)" in lines
    # Companies stay when their tickers go: the old Exxon Mobil Corp keeps its row.
    exxon, holdings = await companies.get(conn, 34088), await companies.get(conn, 2115436)
    assert exxon is not None and exxon.name == "EXXON MOBIL CORP"
    assert holdings is not None and holdings.name == "ExxonMobil Holdings Corp"


async def test_the_same_lists_again_change_nothing(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master(before_routes(), as_of=BEFORE)
    await load_master(as_of=AS_OF)
    rows = await all_rows(conn)

    report = await load_master(as_of=date(2026, 10, 26))

    assert await all_rows(conn) == rows
    changes = report.securities
    assert (changes.opened, changes.closed, changes.updated, changes.deleted) == ((), (), (), ())


async def test_lists_loaded_twice_on_one_day_change_rows_in_place(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master(before_routes(), as_of=AS_OF)

    report = await load_master(as_of=AS_OF)

    assert await history(conn, "XOM") == [(2115436, AS_OF, None)]
    assert await history(conn, "DLST") == []
    assert [row.ticker for row in report.securities.deleted] == ["DLST"]
    assert report.securities.closed == ()


async def test_a_universe_ticker_no_longer_listed_keeps_its_company(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    universe = TEST_UNIVERSE_CSV + "DLST,made-up company that stops being listed\n"
    await load_master(before_routes(), universe=universe, as_of=BEFORE)

    report = await load_master(universe=universe, as_of=AS_OF)
    later = await load_master(universe=universe, as_of=date(2026, 10, 26))

    for run in (report, later):
        assert [(e.ticker, c.cik, c.valid_to) for e, c in run.universe.closed] == [
            ("DLST", 9999999999, AS_OF)
        ]
    company = await companies.get(conn, 9999999999)
    assert company is not None and company.in_universe
    assert (
        "  DLST is no longer listed (since 2026-10-25); CIK 9999999999 stays in the universe "
        "until universe.csv changes" in later.lines("universe.csv")
    )


async def test_a_universe_ticker_that_moves_is_followed_with_a_warning(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    universe = TEST_UNIVERSE_CSV + "XOM,ticker moved to a new CIK\n"
    await load_master(before_routes(), universe=universe, as_of=BEFORE)

    report = await load_master(universe=universe, as_of=AS_OF)

    old, new = await companies.get(conn, 34088), await companies.get(conn, 2115436)
    assert old is not None and not old.in_universe
    assert new is not None and new.in_universe
    assert (
        "  XOM moved from CIK 34088 to CIK 2115436; the universe follows the ticker: check "
        "universe.csv" in report.lines("universe.csv")
    )


async def test_lists_that_look_cut_short_change_nothing_unless_forced(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master()
    rows = await all_rows(conn)
    cut = fixture_routes()
    cut[urls.COMPANY_TICKERS_MF] = (
        b'{"fields":["cik","seriesId","classId","symbol"],"data":[[36405,"S1","C1","VOO"]]}'
    )

    with pytest.raises(SnapshotRefused, match="the fund list would end 444 of 445"):
        await load_master(cut, as_of=date(2026, 10, 26))
    assert await all_rows(conn) == rows

    report = await load_master(cut, as_of=date(2026, 10, 26), force=True)
    assert len(report.securities.closed) == 444


async def test_lists_older_than_the_stored_rows_are_refused(load_master: LoadMaster) -> None:
    await load_master(as_of=AS_OF)

    with pytest.raises(SnapshotRefused, match="newer than this snapshot of 2026-06-30"):
        await load_master(before_routes(), as_of=BEFORE)
