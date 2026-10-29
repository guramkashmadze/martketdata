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
from conftest import (
    SEC_FIXTURES,
    LoadMaster,
    connect_to,
    filing_routes,
    fixture_routes,
    sec_serving,
)

from marketdata.ingest.earnings import EarningsIngest
from marketdata.ingest.facts import FactsLoader
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore

Conn = psycopg.AsyncConnection[Any]
AAPL = 320193
RELEASES = (
    "0000320193-25-000077",  # fiscal 2025 Q4
    "0000320193-26-000005",
    "0000320193-26-000011",
    "0000320193-26-000018",  # fiscal 2026 Q3
)


async def test_every_route_on_apples_filings(
    conn: Conn,
    api: httpx2.AsyncClient,
    api_key: str,
    load_master: LoadMaster,
    tmp_path: Path,
) -> None:
    await load_master()
    routes = {
        **fixture_routes(),
        **filing_routes(),
        urls.companyfacts(AAPL): (SEC_FIXTURES / "companyfacts" / "AAPL.json").read_bytes(),
    }
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await FilingsWatcher(fetcher, connect_to(conn), frozenset()).watch(AAPL)
        await FactsLoader(fetcher, connect_to(conn)).refresh(AAPL)
        for accession in RELEASES:
            await EarningsIngest(fetcher, connect_to(conn)).run(AAPL, accession)
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
