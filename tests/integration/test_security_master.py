"""E2-S2: `SecurityMasterLoader` fills companies and securities from the SEC lists and each
universe company's submissions; a second load with the same files changes nothing.

The SEC is a mock serving the committed fixtures (conftest.py); every write happens in the
test's transaction, which is rolled back afterwards.
"""

from typing import Any

import psycopg
import pytest
from conftest import TEST_UNIVERSE, LoadMaster, fixture_routes

from marketdata.db.repositories import companies, securities
from marketdata.normalize.universe import SecurityOverride
from marketdata.sec import urls

R1 = "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/R1.htm"


async def rows(conn: psycopg.AsyncConnection[Any], table: str) -> list[tuple[Any, ...]]:
    """Every row with its ctid: an UPDATE writes a new row version, so the ctid changes even
    when the values don't."""
    order = "cik" if table == "companies" else "security_id"
    cur = await conn.execute(f"select ctid::text, * from {table} order by {order}")
    return await cur.fetchall()


async def test_load_fills_companies_and_securities(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    report = await load_master()

    current = await securities.list_current(conn)
    assert len(current) == len(report.snapshot.listings) == 495
    assert {row.valid_from for row in current} == {report.as_of}
    assert len(await companies.list_all(conn)) == 32  # 25 exchange-list CIKs and 7 fund-list CIKs
    assert (len(report.securities.opened), report.securities.unchanged) == (495, 0)
    assert (report.companies_added, report.companies_changed) == (32, 0)
    by_ticker = {row.ticker: row for row in current}
    assert (by_ticker["BRK-A"].security_type, by_ticker["BRK-A"].share_class) == ("common", "A")
    assert by_ticker["JPM-PC"].security_type == "preferred"
    assert (by_ticker["VOO"].security_type, by_ticker["VOO"].cik) == ("fund", 36405)
    assert by_ticker["SPCX"].cik == 1181412  # the exchange list's SpaceX, not the fund series
    assert "NONE" not in by_ticker and "NONE-" not in by_ticker


async def test_universe_companies_get_details_and_a_class(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    report = await load_master()

    universe = {company.cik: company for company in await companies.list_in_universe(conn)}
    assert set(universe) == set(TEST_UNIVERSE.values())
    classes = {cik: company.filer_type for cik, company in universe.items()}
    assert classes == {
        320193: "domestic",
        1652044: "domestic",
        27419: "domestic",
        1594805: "domestic",
        1181412: "domestic",
        1222333: "fund",  # SIC 6221, no 8-K item 2.02 (D-052)
        884394: "fund",  # NPORT-P filings
        36405: "fund",  # the fund list
        1046179: "foreign_private_issuer",
        1577552: "foreign_private_issuer",
    }
    target = universe[27419]
    assert (target.name, target.sic_code, target.fiscal_year_end, target.state_of_inc) == (
        "TARGET CORP",
        "5331",
        "0201",
        "MN",
    )
    # The fund list has no names; VOO's registrant takes its name from submissions.
    assert universe[36405].name == "VANGUARD INDEX FUNDS"
    assert universe[1577552].fiscal_year_end is None
    assert report.class_counts == {
        "domestic": 5,
        "foreign_private_issuer": 2,
        "fund": 3,
        "other": 0,
        "unknown": 0,
    }
    assert report.failures == ()


async def test_companies_outside_the_universe(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master()

    jpm = await companies.get(conn, 19617)
    assert jpm is not None
    assert (jpm.name, jpm.filer_type, jpm.in_universe, jpm.sic_code) == (
        "JPMORGAN CHASE & CO",
        "unknown",
        False,
        None,
    )
    proshares = await companies.get(
        conn, 1174610
    )  # in the fund list only: FB is one of its tickers
    assert proshares is not None
    assert (proshares.name, proshares.filer_type) == (None, "fund")


async def test_second_load_changes_nothing(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master()
    before = (await rows(conn, "companies"), await rows(conn, "securities"))

    report = await load_master()

    assert (await rows(conn, "companies"), await rows(conn, "securities")) == before
    assert (report.companies_added, report.companies_changed) == (0, 0)
    assert (len(report.securities.opened), len(report.securities.updated)) == (0, 0)
    assert report.securities.unchanged == 495


async def test_failed_submissions_are_reported_and_keep_what_was_stored(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master()
    before = await companies.get(conn, 1577552)
    routes = fixture_routes()
    routes[urls.submissions(1577552)] = 404

    report = await load_master(routes)

    (failure,) = report.failures
    assert failure.cik == 1577552 and "404" in (failure.error or "")
    assert await companies.get(conn, 1577552) == before
    assert report.companies_changed == 0


async def test_unknown_universe_tickers_are_reported(load_master: LoadMaster) -> None:
    report = await load_master(universe="ticker,note\nAAPL,x\nNOPE,y\n")

    assert [entry.ticker for entry in report.universe.unknown] == ["NOPE"]
    assert "  Unknown tickers (in no SEC list): NOPE" in report.lines("universe.csv")


async def test_overrides_set_the_security_type(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master()

    report = await load_master(overrides=[SecurityOverride(2, "GOOGM", "preferred", R1)])

    (update,) = report.securities.updated
    assert update.listing.ticker == "GOOGM"
    (googm,) = await securities.find_current(conn, ["GOOGM"])
    assert googm.security_type == "preferred"


@pytest.mark.parametrize(
    "statement",
    [
        "insert into companies (cik, name, fiscal_year_end) values (1, 'x', '1332')",
        "insert into companies (cik, name, filer_type) values (1, 'x', 'bank')",
        "insert into securities (cik, ticker, valid_from) values (320193, 'brk.b', '2026-10-25')",
        "insert into securities (cik, ticker, valid_from, valid_to)"
        " values (320193, 'X', '2026-10-25', '2026-10-25')",
    ],
)
async def test_tables_check_their_values(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster, statement: str
) -> None:
    await load_master()

    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute(statement)


async def test_one_current_row_per_ticker(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster
) -> None:
    await load_master()

    with pytest.raises(psycopg.errors.UniqueViolation):
        await conn.execute(
            "insert into securities (cik, ticker, valid_from) values (27419, 'AAPL', '2026-10-26')"
        )
