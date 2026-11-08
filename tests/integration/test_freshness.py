"""D-135: every answer about a company, a comparison or a 13F manager says when the SEC was last
checked for the data it depends on (`checked_at`) and whether that check is more than a day old
or never ran (`check_overdue`): in the services, the REST envelope, the MCP answers, the company
page and the status report. Filings, Forms 4 and releases go by the company's watch; financials
also wait for the newest 10-K or 10-Q's facts; 13F answers go by the managers' watches."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import ACME, BETA, BETA_10Q, MANAGER, OUTSIDE, Q2_10Q, seed
from fastapi import FastAPI
from mcp_client import connected, parsed, text_of
from web_pages import basic

from marketdata.service.companies import coverage
from marketdata.service.compare import compare
from marketdata.service.earnings import latest_earnings
from marketdata.service.filings import recent_filings
from marketdata.service.financials import get_financials
from marketdata.service.funds import company_funds, fund_holdings, list_funds
from marketdata.service.insiders import insider_activity
from marketdata.service.snapshot import company_snapshot
from marketdata.service.status import status_report

Conn = psycopg.AsyncConnection[Any]


@pytest.fixture
async def seeded(conn: Conn) -> Conn:
    await seed(conn)
    return conn


async def _now(conn: Conn) -> datetime:
    row = await (await conn.execute("select now()")).fetchone()
    assert row is not None
    moment: datetime = row[0]
    return moment


async def _watched(conn: Conn, cik: int, ago: timedelta) -> datetime:
    """The company's or manager's watch polled the SEC `ago` before now; returns that time."""
    key = f"raw/sec/api/submissions/{cik}/test.json"
    await conn.execute(
        "insert into raw_objects (key, url, sha256, size_bytes) values (%s, %s, %s, 1) "
        "on conflict do nothing",
        [key, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", "0" * 64],
    )
    at = await _now(conn) - ago
    await conn.execute(
        "insert into watch_state (cik, sha256, raw_key, checked_at, changed_at) "
        "values (%s, %s, %s, %s, %s) "
        "on conflict (cik) do update set checked_at = excluded.checked_at",
        [cik, "0" * 64, key, at, at],
    )
    return at


async def _facts_of(conn: Conn, cik: int, accession: str) -> None:
    """A fact from this 10-Q is stored: its facts refresh ran."""
    await conn.execute(
        "insert into xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "fy, fp, form, filed_at, accession_no) values "
        "(%s, 'us-gaap', 'Revenues', 'USD', '2026-04-01', '2026-06-30', 1000, 2026, 'Q2', "
        "'10-Q', '2026-08-05', %s)",
        [cik, accession],
    )


async def test_filings_insiders_and_releases_go_by_the_company_watch(seeded: Conn) -> None:
    watched = await _watched(seeded, ACME, timedelta(minutes=10))

    for served in (
        await recent_filings(seeded, "ACME"),
        await insider_activity(seeded, "ACME", days=365),
        await latest_earnings(seeded, "ACME"),
    ):
        assert (served.checked_at, served.check_overdue) == (watched, False)


async def test_a_watch_more_than_a_day_old_is_overdue(seeded: Conn) -> None:
    """AMD on 2026-11-08: the newest rows looked current, but the watch had stopped on
    2026-10-25. The answer now says so."""
    watched = await _watched(seeded, ACME, timedelta(days=14))

    served = await recent_filings(seeded, "ACME")

    assert (served.checked_at, served.check_overdue) == (watched, True)


async def test_a_company_never_watched_was_never_checked(seeded: Conn) -> None:
    served = await recent_filings(seeded, "OUTC")  # outside the universe: not watched

    assert served.data.cik == OUTSIDE
    assert (served.checked_at, served.check_overdue) == (None, True)


async def test_financials_lacking_the_newest_10q_are_checked_when_it_was_found(
    seeded: Conn,
) -> None:
    """The watch runs, but the newest 10-Q's facts aren't stored: the financials lack it since
    the watch found it, three days ago. Once its facts are in, the watch decides again."""
    watched = await _watched(seeded, ACME, timedelta(minutes=10))
    found = await _now(seeded) - timedelta(days=3)
    await seeded.execute(
        "update filings set discovered_at = %s where accession_no = %s", [found, Q2_10Q]
    )

    stale = await get_financials(seeded, "ACME")
    snapshot = await company_snapshot(seeded, "ACME")
    filings = await recent_filings(seeded, "ACME")
    await _facts_of(seeded, ACME, Q2_10Q)
    loaded = await get_financials(seeded, "ACME")

    assert (stale.checked_at, stale.check_overdue) == (found, True)
    assert (snapshot.checked_at, snapshot.check_overdue) == (found, True)
    assert (filings.checked_at, filings.check_overdue) == (watched, False)  # filings are current
    assert (loaded.checked_at, loaded.check_overdue) == (watched, False)


async def test_a_comparison_is_as_fresh_as_its_least_recently_checked_company(
    seeded: Conn,
) -> None:
    await _watched(seeded, ACME, timedelta(minutes=10))
    beta = await _watched(seeded, BETA, timedelta(days=2))
    await _facts_of(seeded, ACME, Q2_10Q)
    await _facts_of(seeded, BETA, BETA_10Q)

    served = await compare(seeded, ["ACME", "BETA"])

    assert (served.checked_at, served.check_overdue) == (beta, True)


async def test_13f_answers_go_by_the_managers_watches(seeded: Conn) -> None:
    unwatched = await company_funds(seeded, "ACME")
    watched = await _watched(seeded, MANAGER, timedelta(hours=3))

    for served in (
        await fund_holdings(seeded, MANAGER),
        await company_funds(seeded, "ACME"),
        await list_funds(seeded),
    ):
        assert (served.checked_at, served.check_overdue) == (watched, False)
    assert (unwatched.checked_at, unwatched.check_overdue) == (None, True)


async def test_answers_that_depend_on_no_watch_carry_no_check(seeded: Conn) -> None:
    served = await coverage(seeded)

    assert (served.checked_at, served.check_overdue) == (None, None)


async def test_the_status_report_names_the_oldest_watch_and_counts_the_overdue_ones(
    seeded: Conn,
) -> None:
    """The universe is ACME, BETA, FNDX and FPIX; Test Capital is the curated manager."""
    oldest = await _watched(seeded, BETA, timedelta(days=3))
    await _watched(seeded, ACME, timedelta(minutes=10))
    await _watched(seeded, MANAGER, timedelta(minutes=12))

    freshness = (await status_report(seeded)).data.freshness

    assert freshness.oldest_watched_at == oldest
    assert freshness.overdue_watches == 3  # BETA's, and the fund's and the issuer's never ran


# --- The interfaces: REST, MCP and the company page.


async def test_the_rest_envelope_carries_the_check_after_as_of(
    api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)
    watched = await _watched(conn, ACME, timedelta(days=2))
    headers = {"X-API-Key": api_key}

    filings = await api.get("/v1/companies/ACME/filings", headers=headers)
    listed = await api.get("/v1/coverage", headers=headers)

    body = filings.json(parse_float=Decimal)
    assert list(body)[:5] == ["data", "sources", "as_of", "checked_at", "check_overdue"]
    assert datetime.fromisoformat(body["checked_at"]) == watched
    assert body["checked_at"].endswith("Z") and body["check_overdue"] is True
    assert "checked_at" not in listed.json() and "check_overdue" not in listed.json()


async def test_mcp_answers_carry_the_check_and_a_lookup_none(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)
    await _watched(conn, ACME, timedelta(days=14))

    async with connected(api_app, api_key) as client:
        filings = await client.call_tool("recent_filings", {"ticker": "ACME", "days": 365})
        found = await client.call_tool("lookup_company", {"query": "acme"})

    assert filings.is_error is False, text_of(filings)
    answer = parsed(filings)
    assert list(answer)[:5] == ["data", "sources", "as_of", "checked_at", "check_overdue"]
    assert answer["check_overdue"] is True
    assert {"checked_at", "check_overdue"}.isdisjoint(parsed(found))


async def test_the_company_page_says_when_its_filings_were_last_checked(
    api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)
    auth = basic(api_key)
    watched = await _watched(conn, ACME, timedelta(days=14))
    when = watched.astimezone().strftime("%Y-%m-%d")

    overdue = (await api.get("/companies/ACME", headers=auth)).text
    await _watched(conn, ACME, timedelta(minutes=10))
    current = (await api.get("/companies/ACME", headers=auth)).text
    never = (await api.get("/companies/OUTC", headers=auth)).text

    assert "Last checked against the SEC's list of its filings" in overdue
    assert when in overdue and "more than a day ago: newer filings may be missing" in overdue
    assert "Last checked against the SEC's list of its filings" in current
    assert "more than a day ago" not in current
    assert "Not checked against the SEC's list of its filings yet" in never
