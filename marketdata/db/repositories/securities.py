"""The securities table (PLAN.md §7, E2): which CIK lists a ticker, and since when.

A row is current while `valid_to` is null; a partial unique index allows one current row per
ticker. When a ticker goes away or moves to another CIK its row gets a `valid_to`, so history
stays; only a row opened and ended on the same day is deleted (E2-S4, D-054).
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.security_master import Listing, SecurityUpdate
from marketdata.normalize.tickers import SecurityType


class Security(BaseModel):
    model_config = ConfigDict(frozen=True)

    security_id: int
    cik: int
    ticker: str
    security_type: SecurityType
    exchange: str | None
    share_class: str | None
    cusip: str | None
    figi: str | None
    valid_from: date
    valid_to: date | None


_COLUMNS = """
    security_id, cik, ticker, security_type, exchange, share_class, cusip, figi, valid_from,
    valid_to
"""

# Taken by every load for the length of its transaction, so two loads never interleave.
_LOCK = "select pg_advisory_xact_lock(hashtextextended('marketdata.security_master', 0))"

_SELECT_CURRENT = f"select {_COLUMNS} from securities where valid_to is null order by ticker"
_SELECT_CURRENT_BY_TICKERS = f"""
    select {_COLUMNS} from securities
    where valid_to is null and ticker = any(%(tickers)s)
    order by ticker
"""
_SELECT_CURRENT_BY_CIK = f"""
    select {_COLUMNS} from securities where valid_to is null and cik = %(cik)s order by ticker
"""
_SELECT_CURRENT_BY_CIKS = f"""
    select {_COLUMNS} from securities
    where valid_to is null and cik = any(%(ciks)s)
    order by cik, ticker
"""
# Per ticker, the current row if there is one, else the one closed last.
_SELECT_LATEST_BY_TICKERS = f"""
    select distinct on (ticker) {_COLUMNS} from securities
    where ticker = any(%(tickers)s)
    order by ticker, valid_to desc nulls first, valid_from desc
"""

_COPY = "copy securities (cik, ticker, security_type, exchange, share_class, valid_from) from stdin"

_UPDATE = """
    update securities
    set cik = %(cik)s, security_type = %(security_type)s, exchange = %(exchange)s,
        share_class = %(share_class)s
    where security_id = %(security_id)s and valid_to is null
"""

_CLOSE = """
    update securities set valid_to = %(valid_to)s
    where security_id = any(%(ids)s) and valid_to is null
"""

_DELETE = "delete from securities where security_id = any(%(ids)s) and valid_to is null"


async def lock_for_load(conn: AsyncConnection[Any]) -> None:
    """Wait until no other load holds the security master; released when the transaction ends."""
    await conn.execute(_LOCK)


async def list_current(conn: AsyncConnection[Any]) -> list[Security]:
    async with conn.cursor(row_factory=class_row(Security)) as cur:
        await cur.execute(_SELECT_CURRENT)
        return await cur.fetchall()


async def find_current(conn: AsyncConnection[Any], tickers: Sequence[str]) -> list[Security]:
    """The current rows of these canonical tickers; tickers no row has are left out."""
    async with conn.cursor(row_factory=class_row(Security)) as cur:
        await cur.execute(_SELECT_CURRENT_BY_TICKERS, {"tickers": list(tickers)})
        return await cur.fetchall()


async def list_current_for_cik(conn: AsyncConnection[Any], cik: int) -> list[Security]:
    async with conn.cursor(row_factory=class_row(Security)) as cur:
        await cur.execute(_SELECT_CURRENT_BY_CIK, {"cik": cik})
        return await cur.fetchall()


async def list_current_for_ciks(conn: AsyncConnection[Any], ciks: Sequence[int]) -> list[Security]:
    async with conn.cursor(row_factory=class_row(Security)) as cur:
        await cur.execute(_SELECT_CURRENT_BY_CIKS, {"ciks": list(ciks)})
        return await cur.fetchall()


async def latest(conn: AsyncConnection[Any], tickers: Sequence[str]) -> list[Security]:
    """For each of these tickers, its current row, or the row closed last (E2-S4)."""
    async with conn.cursor(row_factory=class_row(Security)) as cur:
        await cur.execute(_SELECT_LATEST_BY_TICKERS, {"tickers": list(tickers)})
        return await cur.fetchall()


async def close(conn: AsyncConnection[Any], security_ids: Sequence[int], valid_to: date) -> None:
    """End current rows on `valid_to`: their ticker went away or moved to another CIK."""
    if security_ids:
        await conn.execute(_CLOSE, {"ids": list(security_ids), "valid_to": valid_to})


async def delete(conn: AsyncConnection[Any], security_ids: Sequence[int]) -> None:
    """Remove current rows that were opened and ended on the same day (D-054)."""
    if security_ids:
        await conn.execute(_DELETE, {"ids": list(security_ids)})


async def insert(conn: AsyncConnection[Any], listings: Sequence[Listing], valid_from: date) -> None:
    """Open a current row for each listing (COPY: the first load opens about 39,000)."""
    if not listings:
        return
    async with conn.cursor() as cur, cur.copy(_COPY) as copy:
        for listing in listings:
            await copy.write_row(
                (
                    listing.cik,
                    listing.ticker,
                    listing.security_type,
                    listing.exchange,
                    listing.share_class,
                    valid_from,
                )
            )


async def update(conn: AsyncConnection[Any], updates: Sequence[SecurityUpdate]) -> None:
    if not updates:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            _UPDATE,
            [
                {
                    "security_id": change.security_id,
                    "cik": change.listing.cik,
                    "security_type": change.listing.security_type,
                    "exchange": change.listing.exchange,
                    "share_class": change.listing.share_class,
                }
                for change in updates
            ],
        )
