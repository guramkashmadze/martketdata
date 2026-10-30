"""How fresh the data is (E5b, /v1/status): the newest time in each pipeline table."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


class Newest(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_accepted_at: datetime | None
    release_verified_at: datetime | None
    metrics_updated_at: datetime | None
    filing_discovered_at: datetime | None
    watched_at: datetime | None
    live_releases: int


_NEWEST = """
    select (select max(accepted_at) from earnings_releases) as release_accepted_at,
           (select max(verified_at) from earnings_releases) as release_verified_at,
           (select max(updated_at) from financial_metrics) as metrics_updated_at,
           (select max(discovered_at) from filings) as filing_discovered_at,
           (select max(checked_at) from watch_state) as watched_at,
           (select count(*) from earnings_releases where live) as live_releases
"""


async def newest(conn: AsyncConnection[Any]) -> Newest:
    async with conn.cursor(row_factory=class_row(Newest)) as cur:
        await cur.execute(_NEWEST)
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("the freshness query returned no row")
    return row
