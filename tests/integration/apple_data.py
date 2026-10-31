"""Apple's real filings, loaded from the committed fixtures as the worker loads them (E5a-S3; the
MCP tools' size checks reuse it in E6): the security master, the watcher's filings, a facts
refresh with its metrics and four earnings releases, each cross-checked with the 10-Q or
10-K."""

from pathlib import Path
from typing import Any

import psycopg
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

AAPL = 320193
RELEASES = (
    "0000320193-25-000077",  # fiscal 2025 Q4
    "0000320193-26-000005",
    "0000320193-26-000011",
    "0000320193-26-000018",  # fiscal 2026 Q3
)


async def load_apple(
    conn: psycopg.AsyncConnection[Any], load_master: LoadMaster, tmp_path: Path
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
