"""E9-S6: the MCP tools investor_portfolio and fund_holders (§10.3): the API's portfolios in a
compact answer under 8 KB, even for a manager of hundreds of positions; the largest 20 and at
most 6 of each change type; holders by manager name, each with its report's link; an unknown
manager answered with the curated ones."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
from api_seed import MANAGER, MANAGER_13F, seed
from fastapi import FastAPI
from fund_world import APPLE, Q1, Q2, Line, curate, report
from mcp_client import connected, parsed, text_of

from marketdata.mcp_server.tools import PORTFOLIO_CHANGES, PORTFOLIO_LARGEST

Conn = psycopg.AsyncConnection[Any]
MAX_ANSWER = 8 * 1024
BUSY = 1037389


async def test_investor_portfolio_is_the_apis_in_short(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("investor_portfolio", {"manager": "test capital"})
    rest = (await api.get(f"/v1/funds/{MANAGER}/holdings", headers={"X-API-Key": api_key})).json(
        parse_float=Decimal
    )

    assert result.is_error is False, text_of(result)
    answer = parsed(result)
    assert set(answer) == {"data", "sources", "as_of", "disclaimer"}
    for key in ("sources", "as_of", "disclaimer"):
        assert answer[key] == rest[key], key
    data = answer["data"]
    assert (data["manager"], data["quarter"], data["report_period"], data["filed_at"]) == (
        "Test Capital",
        "2026Q2",
        "2026-06-30",
        "2026-08-14",
    )
    assert data["compared_with"] is None  # no report for 2026Q1
    assert data["filing_url"].endswith(f"/data/{MANAGER}/000900900026000003/")
    acme, call = data["largest"]
    assert acme == {
        "issuer": "ISSUER",
        "ticker": "ACME",
        "shares": Decimal("40000"),
        "value_usd": Decimal("4000000"),
        "weight_pct": Decimal("100.0000"),
        "flags": ["no_prior_report"],
    }
    assert (call["ticker"], call["put_call"]) == ("BETA", "CALL")
    assert [source["accession_no"] for source in answer["sources"]] == [MANAGER_13F]


async def test_an_unknown_manager_is_answered_with_the_curated_ones(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("investor_portfolio", {"manager": "scion"})

    assert result.is_error is True
    assert "No curated manager matches 'scion'. Curated managers: Test Capital." in text_of(result)


async def test_fund_holders_lists_managers_by_name_with_their_reports(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("fund_holders", {"ticker": "acme"})

    assert result.is_error is False, text_of(result)
    answer = parsed(result)
    assert answer["sources"] == []  # each holder carries its report's link
    [holder] = answer["data"]["holders"]
    assert (holder["manager"], holder["quarter"], holder["filed_at"]) == (
        "Test Capital",
        "2026Q2",
        "2026-08-14",
    )
    assert holder["filing_url"].endswith(f"/data/{MANAGER}/000900900026000003/")
    assert holder["positions"] == [
        {
            "ticker": "ACME",
            "shares": Decimal("40000"),
            "value_usd": Decimal("4000000"),
            "weight_pct": Decimal("100.0000"),
            "flags": ["no_prior_report"],
        }
    ]


def _busy(start: int, count: int, scale: int) -> list[Line]:
    """Positions with the long issuer names 13F tables carry."""
    return [
        Line(
            f"{n:08d}X",
            1_000 * n * scale,
            1_000_000 * n * scale,
            f"CONSOLIDATED HOLDINGS GROUP {n:03d}",
        )
        for n in range(start, start + count)
    ]


async def test_a_busy_portfolio_stays_under_8_kb(
    api_app: FastAPI, api_key: str, conn: Conn, tmp_path: Path
) -> None:
    await curate(conn, {BUSY: "Renaissance Technologies"})
    before = [*_busy(1, 300, 1), Line(APPLE, 1, 1)]
    # 100 positions exited, 100 new, 100 doubled, 100 unchanged.
    now = [*_busy(101, 100, 2), *_busy(201, 100, 1), *_busy(401, 100, 1)]
    await report(conn, tmp_path, BUSY, "0000000009-26-000001", date(2026, 5, 15), Q1, before)
    await report(conn, tmp_path, BUSY, "0000000009-26-000002", date(2026, 8, 13), Q2, now)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("investor_portfolio", {"manager": "renaissance"})

    text = text_of(result)
    assert result.is_error is False, text
    assert len(text.encode()) < MAX_ANSWER, len(text.encode())
    data = parsed(result)["data"]
    assert len(data["largest"]) == PORTFOLIO_LARGEST
    assert all(len(data[kind]) == PORTFOLIO_CHANGES for kind in ("new", "added", "exited"))
    assert data["changes"] == {
        "new": 100,
        "added": 100,
        "trimmed": 0,
        "unchanged": 100,
        "exited": 101,
    }
    assert set(data["more"]) == {"largest", "new", "added", "exited"}
