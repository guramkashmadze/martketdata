"""E15-S4 (the E6-S2 follow-up): with PUBLIC_BASE_URL set, MCP answers give their values' proof
pages, the pages of the web UI: a release's address, or one address with {placeholders} filled
in from each value, so an answer stays compact. Without it they give none."""

from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import Q3_RELEASE, seed
from conftest import connect_to
from fastapi import FastAPI
from mcp import Client
from mcp_client import connected, parsed, text_of
from web_pages import basic

from marketdata.api.app import create_app
from marketdata.config import Settings

Conn = psycopg.AsyncConnection[Any]
BASE = "https://md.example.com"


@pytest.fixture
def linked_app(conn: Conn, migrated_database: str) -> FastAPI:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url=migrated_database, public_base_url=f"{BASE}/"
    )
    return create_app(settings, connect=connect_to(conn))


async def _call(client: Client, tool: str, arguments: dict[str, Any]) -> Any:
    result = await client.call_tool(tool, arguments)
    assert result.is_error is False, text_of(result)
    return parsed(result)


async def test_answers_name_the_proof_pages_of_their_values(
    linked_app: FastAPI, conn: Conn, api_key: str
) -> None:
    await seed(conn)

    async with connected(linked_app, api_key) as client:
        earnings = await _call(client, "latest_earnings", {"ticker": "acme"})
        financials = await _call(
            client, "get_financials", {"ticker": "acme", "metrics": ["revenue"], "limit": 2}
        )
        compared = await _call(
            client, "compare_companies", {"tickers": ["ACME", "BETA"], "metrics": ["revenue"]}
        )
        snapshot = await _call(client, "company_snapshot", {"ticker": "ACME"})
        trades = await _call(client, "insider_activity", {"ticker": "ACME"})
        filings = await _call(client, "recent_filings", {"ticker": "ACME"})

    assert earnings["proof_url"] == f"{BASE}/proof/earnings/{Q3_RELEASE}"
    assert financials["proof_url"] == f"{BASE}/proof/metrics/ACME/{{metric}}/Q/{{period_end}}"
    assert compared["proof_url"] == f"{BASE}/proof/metrics/{{ticker}}/{{metric}}/TTM/{{period_end}}"
    assert snapshot["proof_url"] == f"{BASE}/proof/metrics/ACME/{{metric}}/TTM/{{period_end}}"
    assert trades["proof_url"] == f"{BASE}/proof/insiders/{{accession_no}}"
    assert "proof_url" not in filings  # filings link to sec.gov themselves
    assert list(earnings)[-1] == "disclaimer"

    # Filled in from the answer, each address opens its page in the web UI.
    series = financials["data"]["metrics"][0]
    value = series["values"][0]
    trade = trades["data"]["trades"][0]
    pages = [
        earnings["proof_url"],
        financials["proof_url"].format(metric=series["metric"], period_end=value["period_end"]),
        compared["proof_url"].format(ticker="BETA", metric="revenue", period_end="2026-06-30"),
        trades["proof_url"].format(accession_no=trade["accession_no"]),
    ]
    transport = httpx2.ASGITransport(app=linked_app)
    async with httpx2.AsyncClient(transport=transport, base_url=BASE) as ui:
        for url in pages:
            response = await ui.get(url.removeprefix(BASE), headers=basic(api_key))
            assert response.status_code == 200, url


async def test_without_a_public_address_answers_name_no_page(
    api_app: FastAPI, conn: Conn, api_key: str
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        answer = await _call(client, "latest_earnings", {"ticker": "ACME"})

    assert "proof_url" not in answer
