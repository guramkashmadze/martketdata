"""Process and data status: /health (E0) and /v1/status (E5b; E7c and E11 add to it).

/v1/status needs no key. It publishes how fresh the data is (the newest release, metric row,
filing and watch, the oldest watch and how many are overdue, D-135) and the accuracy report
(§9.10, service/accuracy.py), from clean tables. The web UI's status page (E15-S4) shows the
same report; both reuse one for a minute (StatusCache).
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
import structlog
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.pool import Pool
from marketdata.db.repositories import freshness, health
from marketdata.normalize.freshness import check_overdue
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
    # D-135: the least recent watch of a universe company or curated manager (null when none
    # was ever watched), and how many of them were last watched more than a day ago, or never.
    oldest_watched_at: datetime | None
    overdue_watches: int
    live_releases: int  # releases the watcher routed when they were filed


class StatusReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    freshness: Freshness
    accuracy: AccuracyReport


async def status_report(
    conn: AsyncConnection[Any], *, now: datetime | None = None
) -> Served[StatusReport]:
    found = await freshness.newest(conn)
    watched = await freshness.watched(conn)
    now = now or datetime.now(UTC)
    checks = [row.checked_at for row in watched if row.checked_at is not None]
    report = StatusReport(
        freshness=Freshness(
            **found.model_dump(),
            oldest_watched_at=min(checks, default=None),
            overdue_watches=sum(check_overdue(row.checked_at, now) for row in watched),
        ),
        accuracy=await accuracy_report(conn),
    )
    return Served(
        data=report,
        sources=(),
        as_of=newest(
            [found.release_verified_at, found.metrics_updated_at, found.filing_discovered_at]
        ),
        quality_flags=(),
    )


CACHE_SECONDS = 60.0


class StatusCache:
    """The latest status report, reused for a minute: /v1/status needs no key and the status page
    may be public, so the report's work stays bounded (D-105)."""

    def __init__(
        self, seconds: float = CACHE_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.seconds = seconds
        self._clock = clock
        self._kept: tuple[float, Served[StatusReport]] | None = None

    async def report(self, conn: AsyncConnection[Any]) -> Served[StatusReport]:
        if self._kept is not None and self._clock() - self._kept[0] < self.seconds:
            return self._kept[1]
        served = await status_report(conn)
        self._kept = (self._clock(), served)
        return served
