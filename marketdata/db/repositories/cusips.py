"""The cusip_map table (E9-S3, PLAN.md §7): what OpenFIGI said about each CUSIP of a 13F
filing, linked to our security with that ticker."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.cusips import CusipMatch


@dataclass(frozen=True)
class MappedCusip:
    """A mapping to store: OpenFIGI's answer, our security and the response it came from."""

    match: CusipMatch
    security_id: int | None
    raw_key: str | None


class CusipMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    cusip: str
    status: str
    security_id: int | None
    figi: str | None
    ticker: str | None
    name: str | None
    exch_code: str | None
    security_type: str | None
    market_sector: str | None
    candidates: int
    note: str | None
    raw_key: str | None
    matched_at: datetime
    checked_at: datetime


_COLUMNS = """
    cusip, status, security_id, figi, ticker, name, exch_code, security_type, market_sector,
    candidates, note, raw_key, matched_at, checked_at
"""

# CUSIPs in stored 13F lines that were never asked about, or not matched when last asked
# before `before`; a matched CUSIP isn't asked again.
_PENDING = """
    select distinct h.cusip from holdings_raw h
    left join cusip_map m using (cusip)
    where m.cusip is null or (m.status <> 'matched' and m.checked_at < %(before)s)
    order by h.cusip
"""

_UPSERT = """
    insert into cusip_map (
      cusip, status, security_id, figi, ticker, name, exch_code, security_type, market_sector,
      candidates, note, raw_key
    ) values (
      %(cusip)s, %(status)s, %(security_id)s, %(figi)s, %(ticker)s, %(name)s, %(exch_code)s,
      %(security_type)s, %(market_sector)s, %(candidates)s, %(note)s, %(raw_key)s
    )
    on conflict (cusip) do update set
      status = excluded.status, security_id = excluded.security_id, figi = excluded.figi,
      ticker = excluded.ticker, name = excluded.name, exch_code = excluded.exch_code,
      security_type = excluded.security_type, market_sector = excluded.market_sector,
      candidates = excluded.candidates, note = excluded.note, raw_key = excluded.raw_key,
      checked_at = now(),
      matched_at = case
        when (cusip_map.status, cusip_map.security_id, cusip_map.figi, cusip_map.ticker)
          is distinct from (excluded.status, excluded.security_id, excluded.figi, excluded.ticker)
        then now() else cusip_map.matched_at end
"""

_GET_MANY = f"select {_COLUMNS} from cusip_map where cusip = any(%(cusips)s) order by cusip"
_OF_SECURITIES = f"""
    select {_COLUMNS} from cusip_map where security_id = any(%(ids)s) order by cusip
"""
_UNLINKED = f"""
    select {_COLUMNS} from cusip_map
    where status = 'matched' and security_id is null and ticker is not null order by cusip
"""
_SET_SECURITY = """
    update cusip_map set security_id = %(security_id)s, matched_at = now()
    where cusip = %(cusip)s and security_id is distinct from %(security_id)s
"""
_COUNTS = "select status, count(*) from cusip_map group by status order by status"


async def pending(conn: AsyncConnection[Any], recheck_before: datetime) -> list[str]:
    """CUSIPs of stored 13F lines to ask OpenFIGI about: never asked, or not matched when last
    asked before `recheck_before`."""
    async with conn.cursor() as cur:
        await cur.execute(_PENDING, {"before": recheck_before})
        return [str(row[0]) for row in await cur.fetchall()]


async def upsert(conn: AsyncConnection[Any], mapped: Sequence[MappedCusip]) -> None:
    rows = [
        {
            "cusip": item.match.cusip,
            "status": item.match.status,
            "security_id": item.security_id,
            "figi": item.match.figi,
            "ticker": item.match.ticker,
            "name": item.match.name,
            "exch_code": item.match.exch_code,
            "security_type": item.match.security_type,
            "market_sector": item.match.market_sector,
            "candidates": item.match.candidates,
            "note": item.match.note,
            "raw_key": item.raw_key,
        }
        for item in mapped
    ]
    if rows:
        async with conn.cursor() as cur:
            await cur.executemany(_UPSERT, rows)


async def get_many(conn: AsyncConnection[Any], cusips: Sequence[str]) -> list[CusipMapping]:
    async with conn.cursor(row_factory=class_row(CusipMapping)) as cur:
        await cur.execute(_GET_MANY, {"cusips": list(cusips)})
        return await cur.fetchall()


async def of_securities(
    conn: AsyncConnection[Any], security_ids: Sequence[int]
) -> list[CusipMapping]:
    """The CUSIPs mapped to these securities (a company's share classes)."""
    async with conn.cursor(row_factory=class_row(CusipMapping)) as cur:
        await cur.execute(_OF_SECURITIES, {"ids": list(security_ids)})
        return await cur.fetchall()


async def unlinked(conn: AsyncConnection[Any]) -> list[CusipMapping]:
    """Matches with a ticker but no security of ours (yet)."""
    async with conn.cursor(row_factory=class_row(CusipMapping)) as cur:
        await cur.execute(_UNLINKED)
        return await cur.fetchall()


async def set_securities(conn: AsyncConnection[Any], links: Sequence[tuple[str, int]]) -> None:
    """Link these CUSIPs to these securities."""
    if links:
        async with conn.cursor() as cur:
            await cur.executemany(
                _SET_SECURITY, [{"cusip": cusip, "security_id": sid} for cusip, sid in links]
            )


async def counts(conn: AsyncConnection[Any]) -> dict[str, int]:
    async with conn.cursor() as cur:
        await cur.execute(_COUNTS)
        return {str(status): int(count) for status, count in await cur.fetchall()}
