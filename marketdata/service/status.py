"""Process and data status: /health (E0); /v1/status is added in E7c, E5b and E11."""

import asyncio
from typing import Literal

import psycopg
import structlog

from marketdata.db.pool import Pool
from marketdata.db.repositories import health

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
