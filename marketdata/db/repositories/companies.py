"""The companies table (PLAN.md §7, E2): one row per CIK in the SEC lists or the universe."""

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.security_master import CompanyRow, FilerType


class Company(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int
    name: str | None
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_inc: str | None
    filer_type: FilerType
    in_universe: bool
    updated_at: datetime


_COLUMNS = """
    cik, name, sic_code, sic_description, fiscal_year_end, state_of_inc, filer_type,
    in_universe, updated_at
"""

# updated_at moves only when a value changes, so writing an unchanged row changes nothing.
_UPSERT = """
    insert into companies (
      cik, name, sic_code, sic_description, fiscal_year_end, state_of_inc, filer_type,
      in_universe
    )
    values (
      %(cik)s, %(name)s, %(sic_code)s, %(sic_description)s, %(fiscal_year_end)s,
      %(state_of_inc)s, %(filer_type)s, %(in_universe)s
    )
    on conflict (cik) do update set
      name = excluded.name,
      sic_code = excluded.sic_code,
      sic_description = excluded.sic_description,
      fiscal_year_end = excluded.fiscal_year_end,
      state_of_inc = excluded.state_of_inc,
      filer_type = excluded.filer_type,
      in_universe = excluded.in_universe,
      updated_at = now()
    where (
      companies.name, companies.sic_code, companies.sic_description,
      companies.fiscal_year_end, companies.state_of_inc, companies.filer_type,
      companies.in_universe
    ) is distinct from (
      excluded.name, excluded.sic_code, excluded.sic_description, excluded.fiscal_year_end,
      excluded.state_of_inc, excluded.filer_type, excluded.in_universe
    )
"""

_SELECT_ALL = f"select {_COLUMNS} from companies order by cik"
_SELECT_ONE = f"select {_COLUMNS} from companies where cik = %(cik)s"
_SELECT_MANY = f"select {_COLUMNS} from companies where cik = any(%(ciks)s) order by cik"
_SELECT_IN_UNIVERSE = f"select {_COLUMNS} from companies where in_universe order by cik"


async def upsert(conn: AsyncConnection[Any], rows: Sequence[CompanyRow]) -> None:
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(_UPSERT, [asdict(row) for row in rows])


async def list_all(conn: AsyncConnection[Any]) -> list[Company]:
    async with conn.cursor(row_factory=class_row(Company)) as cur:
        await cur.execute(_SELECT_ALL)
        return await cur.fetchall()


async def get(conn: AsyncConnection[Any], cik: int) -> Company | None:
    async with conn.cursor(row_factory=class_row(Company)) as cur:
        await cur.execute(_SELECT_ONE, {"cik": cik})
        return await cur.fetchone()


async def get_many(conn: AsyncConnection[Any], ciks: Sequence[int]) -> list[Company]:
    async with conn.cursor(row_factory=class_row(Company)) as cur:
        await cur.execute(_SELECT_MANY, {"ciks": list(ciks)})
        return await cur.fetchall()


async def list_in_universe(conn: AsyncConnection[Any]) -> list[Company]:
    async with conn.cursor(row_factory=class_row(Company)) as cur:
        await cur.execute(_SELECT_IN_UNIVERSE)
        return await cur.fetchall()


# --- E6: companies found by ticker, CIK or name (the MCP tool lookup_company).


class Found(BaseModel):
    """A company a search found: `rank` says how (lower is closer), `ticker` is the current
    ticker that matched, for a ticker match."""

    model_config = ConfigDict(frozen=True)

    cik: int
    name: str | None
    filer_type: FilerType
    in_universe: bool
    updated_at: datetime
    rank: int
    ticker: str | None


# Ranks: 0 the ticker; 1 a share class of it (BRK finds BRK-A and BRK-B); 2 the CIK; 3 the whole
# name; 4 the name starts with the words; 5 it starts with them inside a word; 6 a word in it
# starts with them; 7 they are elsewhere in it. A name is compared as its lowercase letters and
# digits, every other run of characters one space, as service/companies.py writes `words`.
# Companies without a current ticker are left out: every tool takes a ticker.
_SEARCH = """
    with found as (
      select cik, 0 as rank, ticker from securities
       where valid_to is null and ticker = %(ticker)s::text
      union all
      select cik, 1, ticker from securities
       where valid_to is null and ticker like %(ticker)s::text || '-%%'
      union all
      select cik, 2, null from companies where cik = %(cik)s::bigint
      union all
      select cik,
             case when words = %(words)s::text then 3
                  when words like %(words)s::text || ' %%' then 4
                  when words like %(words)s::text || '%%' then 5
                  when ' ' || words like '%% ' || %(words)s::text || '%%' then 6
                  else 7 end,
             null
        from (select cik, trim(regexp_replace(lower(name), '[^a-z0-9]+', ' ', 'g')) as words
                from companies where name is not null) named
       where words like '%%' || %(words)s::text || '%%'
    ),
    best as (
      select distinct on (cik) cik, rank, ticker
        from found
       where exists (select 1 from securities s where s.cik = found.cik and s.valid_to is null)
       order by cik, rank, ticker
    )
    select c.cik, c.name, c.filer_type, c.in_universe, c.updated_at, best.rank, best.ticker
      from best join companies c using (cik)
     order by best.rank, c.in_universe desc, c.name, c.cik
     limit %(limit)s
"""


async def search(
    conn: AsyncConnection[Any],
    *,
    words: str | None,
    ticker: str | None,
    cik: int | None,
    limit: int,
) -> list[Found]:
    """Companies with a current ticker whose ticker, CIK or name matches, closest first, then
    universe companies first, then by name. `words` are lowercase letters and digits separated
    by single spaces; None leaves the name out, as None leaves out the ticker or the CIK."""
    async with conn.cursor(row_factory=class_row(Found)) as cur:
        await cur.execute(_SEARCH, {"words": words, "ticker": ticker, "cik": cik, "limit": limit})
        return await cur.fetchall()
