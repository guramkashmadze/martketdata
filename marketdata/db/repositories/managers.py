"""The managers table (E9-S1, PLAN.md §7): the 13F filers loaded from data/managers.csv.

`marketdata managers load` writes the file's managers, curated, each with the name EDGAR gives
it. A manager the file no longer lists stays, not curated, so the holdings it filed keep their
manager.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class CuratedManager:
    """A manager to write: its CIK, EDGAR's name, our display name and the file's note."""

    manager_cik: int
    name: str
    display_name: str
    notes: str


class Manager(BaseModel):
    model_config = ConfigDict(frozen=True)

    manager_cik: int
    name: str
    display_name: str
    curated: bool
    notes: str | None
    updated_at: datetime


_COLUMNS = "manager_cik, name, display_name, curated, notes, updated_at"

# Managers the file no longer lists go first, so one dropped can hand its display name on.
_UNCURATE = """
    update managers set curated = false, updated_at = now()
     where curated and manager_cik <> all(%(ciks)s::bigint[])
    returning manager_cik
"""

_UPSERT = """
    insert into managers (manager_cik, name, display_name, curated, notes)
    select cik, name, display_name, true, notes
      from unnest(%(cik)s::bigint[], %(name)s::text[], %(display_name)s::text[],
                  %(notes)s::text[]) as listed (cik, name, display_name, notes)
    on conflict (manager_cik) do update set
      name = excluded.name,
      display_name = excluded.display_name,
      curated = true,
      notes = excluded.notes,
      updated_at = now()
    where (managers.name, managers.display_name, managers.curated, managers.notes)
      is distinct from (excluded.name, excluded.display_name, true, excluded.notes)
"""

_LIST = f"select {_COLUMNS} from managers where curated or not %(curated)s order by display_name"
_GET = f"select {_COLUMNS} from managers where manager_cik = %(cik)s"
_CURATED_CIKS = "select manager_cik from managers where curated order by manager_cik"


async def replace_curated(
    conn: AsyncConnection[Any], listed: Sequence[int], written: Sequence[CuratedManager]
) -> tuple[int, list[int]]:
    """Curate exactly the managers `listed` (the file's CIKs) and write `written`, the ones
    checked on EDGAR; a listed manager not written keeps its stored row. Returns the rows
    written and the CIKs no longer curated."""
    async with conn.cursor() as cur:
        await cur.execute(_UNCURATE, {"ciks": list(listed)})
        uncurated = sorted(int(row[0]) for row in await cur.fetchall())
        await cur.execute(
            _UPSERT,
            {
                "cik": [manager.manager_cik for manager in written],
                "name": [manager.name for manager in written],
                "display_name": [manager.display_name for manager in written],
                "notes": [manager.notes for manager in written],
            },
        )
        return cur.rowcount, uncurated


async def list_managers(conn: AsyncConnection[Any], *, curated: bool = True) -> list[Manager]:
    """The managers by display name: the curated ones, or all with `curated=False`."""
    async with conn.cursor(row_factory=class_row(Manager)) as cur:
        await cur.execute(_LIST, {"curated": curated})
        return await cur.fetchall()


async def get(conn: AsyncConnection[Any], cik: int) -> Manager | None:
    async with conn.cursor(row_factory=class_row(Manager)) as cur:
        await cur.execute(_GET, {"cik": cik})
        return await cur.fetchone()


async def curated_ciks(conn: AsyncConnection[Any]) -> list[int]:
    async with conn.cursor() as cur:
        await cur.execute(_CURATED_CIKS)
        return [int(row[0]) for row in await cur.fetchall()]
