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
