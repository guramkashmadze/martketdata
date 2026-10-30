"""Process and data status: /health (E0) and /v1/status (E5b; E7c and E11 add to it).

/v1/status needs no key. It publishes how fresh the data is (the newest release, metric row,
filing and watch) and the accuracy report (§9.10, service/accuracy.py), from clean tables.
"""

import asyncio
from datetime import datetime
from typing import Any, Literal

import psycopg
import structlog
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.pool import Pool
from marketdata.db.repositories import freshness, health
from marketdata.service.accuracy import AccuracyReport, accuracy_report
from marketdata.service.served import Served, newest

DatabaseStatus = Literal["ok", "down"]

log = structlog.get_logger(__name__)


async def database_status(pool: Pool, timeout_s: float = 2.0) -> DatabaseStatus:
    """'ok' if a pooled connection answers within timeout_s, else 'down'."""
    try:
        async with asyncio.timeout(timeout_s), pool.connection() as conn:
            await health.ping(conn)
    except (TimeoutError, psycopg.Error) as exc:
        log.warning("database_unreachable", error=str(exc) or type(exc).__name__)
        return "down"
    return "ok"


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_accepted_at: datetime | None  # the newest earnings release the SEC accepted
    release_verified_at: datetime | None  # the latest first verification of a release
    metrics_updated_at: datetime | None  # the latest change to a metric row
    filing_discovered_at: datetime | None  # the latest filing a watch or backfill stored
    watched_at: datetime | None  # the latest watch of a company's filings
    live_releases: int  # releases the watcher routed when they were filed


class StatusReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    freshness: Freshness
    accuracy: AccuracyReport


async def status_report(conn: AsyncConnection[Any]) -> Served[StatusReport]:
    found = await freshness.newest(conn)
    report = StatusReport(
        freshness=Freshness(**found.model_dump()), accuracy=await accuracy_report(conn)
    )
    return Served(
        data=report,
        sources=(),
        as_of=newest(
            [found.release_verified_at, found.metrics_updated_at, found.filing_discovered_at]
        ),
        quality_flags=(),
    )
