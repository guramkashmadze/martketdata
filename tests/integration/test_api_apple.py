"""E5a-S3: every /v1 route on Apple's real filings, loaded from the committed fixtures as the
worker loads them: the security master, the watcher's filings, a facts refresh with its metrics
and four earnings releases, each cross-checked with the 10-Q or 10-K. Every value served carries
a complete proof object (§9.10)."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
from api_contract import check_envelope
from apple_data import RELEASES, load_apple
from conftest import LoadMaster

Conn = psycopg.AsyncConnection[Any]


async def test_every_route_on_apples_filings(
    conn: Conn,
    api: httpx2.AsyncClient,
    api_key: str,
    load_master: LoadMaster,
    tmp_path: Path,
) -> None:
    await load_apple(conn, load_master, tmp_path)
    headers = {"X-API-Key": api_key}
    requests: list[tuple[str, dict[str, str]]] = [
        ("/v1/coverage", {"limit": "100"}),
        ("/v1/companies/aapl", {}),
        ("/v1/companies/AAPL/earnings/latest", {}),
        ("/v1/companies/AAPL/financials", {"period": "Q", "limit": "12"}),
        ("/v1/companies/AAPL/financials", {"period": "FY"}),
        ("/v1/companies/AAPL/financials", {"period": "TTM"}),
        ("/v1/companies/AAPL/filings", {"limit": "100"}),
        ("/v1/earnings", {"ticker": "AAPL"}),
        (f"/v1/earnings/{RELEASES[0]}", {}),
        ("/v1/compare", {"tickers": "AAPL,GOOGL"}),
    ]

    bodies = {}
    for path, params in requests:
        response = await api.get(path, params=params, headers=headers)
        assert response.status_code == 200, (path, response.text)
        bodies[path, tuple(params.items())] = body = response.json(parse_float=Decimal)
        check_envelope(body)

    latest = bodies["/v1/companies/AAPL/earnings/latest", ()]["data"]
    assert (latest["release"]["accession_no"], latest["period"]["fiscal_period"]) == (
        RELEASES[-1],
        "Q3",
    )
    revenue = latest["facts"][0]
    assert (revenue["slot"], revenue["value"]) == ("revenue", Decimal("109417000000"))
    assert revenue["proof"]["cross_check"]["status"] == "matched"
    feed = bodies["/v1/earnings", (("ticker", "AAPL"),)]["data"]["releases"]
    assert [release["release"]["accession_no"] for release in feed] == list(reversed(RELEASES))
    snapshot = bodies["/v1/companies/aapl", ()]["data"]
    assert snapshot["ttm"] and snapshot["latest_period"]["fiscal_year"] == 2026
    quarters = bodies["/v1/companies/AAPL/financials", (("period", "Q"), ("limit", "12"))]
    assert len(quarters["sources"]) > 1 and quarters["as_of"]
