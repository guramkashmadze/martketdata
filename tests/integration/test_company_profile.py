"""E2-S5: `capabilities(conn, cik)` and `company_profile(conn, cik)` on a security master seeded
from the fixtures: a domestic company, an ETF and a foreign issuer."""

from typing import Any

import psycopg
import pytest
from conftest import LoadMaster

from marketdata.service.companies import capabilities, company_profile
from marketdata.service.errors import NotFound


@pytest.fixture
async def seeded(load_master: LoadMaster) -> None:
    await load_master()


async def test_a_domestic_company_gets_earnings_and_fundamentals(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    apple = await capabilities(conn, 320193)

    assert (apple.filer_type, apple.in_universe) == ("domestic", True)
    assert apple.of("earnings").applies and apple.of("fundamentals").applies


async def test_an_etf_gets_neither_with_the_reason(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    voo = await capabilities(conn, 36405)

    assert voo.filer_type == "fund"
    assert not voo.of("earnings").applies and not voo.of("fundamentals").applies
    assert voo.of("fundamentals").reason == "A fund: fundamentals don't apply."


async def test_a_foreign_issuer_gets_neither_with_the_reason(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    tsm = await capabilities(conn, 1046179)

    assert tsm.filer_type == "foreign_private_issuer"
    assert not tsm.of("earnings").applies
    assert tsm.of("earnings").reason.startswith("A foreign private issuer: its results come in 6-K")


async def test_an_unknown_cik_is_not_found(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    with pytest.raises(NotFound, match="No company has CIK 42"):
        await capabilities(conn, 42)


async def test_the_profile_lists_the_company_and_its_current_tickers(
    conn: psycopg.AsyncConnection[Any], seeded: None
) -> None:
    profile = await company_profile(conn, 1652044)

    assert (profile.name, profile.filer_type, profile.sic_code, profile.fiscal_year_end) == (
        "Alphabet Inc.",
        "domestic",
        "7370",
        "1231",
    )
    assert [s.ticker for s in profile.securities] == ["GOOG", "GOOGL", "GOOGM", "GOOGN"]
    assert profile.capabilities.of("earnings").applies
