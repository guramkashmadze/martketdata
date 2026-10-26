"""The company_predecessors table (D-068, E7a-S1): a company's history filed under an earlier CIK.

Loaded from data/predecessors.csv by the security-master load, which writes only links that
changed and removes links the file no longer has.
"""

from collections.abc import Sequence
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


class CompanyPredecessor(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int
    predecessor_cik: int
    source_url: str
    note: str


_UPSERT = """
    insert into company_predecessors (cik, predecessor_cik, source_url, note)
    select * from unnest(
      %(cik)s::bigint[], %(predecessor_cik)s::bigint[], %(source_url)s::text[], %(note)s::text[]
    )
    on conflict (cik, predecessor_cik) do update set
      source_url = excluded.source_url,
      note = excluded.note
    where (company_predecessors.source_url, company_predecessors.note)
      is distinct from (excluded.source_url, excluded.note)
"""

_DELETE_OTHERS = """
    delete from company_predecessors
    where (cik, predecessor_cik) not in (
      select * from unnest(%(cik)s::bigint[], %(predecessor_cik)s::bigint[])
    )
"""

_LIST = """
    select cik, predecessor_cik, source_url, note from company_predecessors
    order by cik, predecessor_cik
"""

_OF = """
    select predecessor_cik from company_predecessors where cik = %(cik)s order by predecessor_cik
"""

_SUCCESSORS = """
    select predecessor_cik, cik from company_predecessors where predecessor_cik = any(%(ciks)s)
"""


async def replace_all(
    conn: AsyncConnection[Any], links: Sequence[CompanyPredecessor]
) -> tuple[int, int]:
    """Make the table hold exactly `links`; returns (rows written, rows removed)."""
    params = {
        "cik": [link.cik for link in links],
        "predecessor_cik": [link.predecessor_cik for link in links],
        "source_url": [link.source_url for link in links],
        "note": [link.note for link in links],
    }
    async with conn.cursor() as cur:
        await cur.execute(_UPSERT, params)
        written = cur.rowcount
        await cur.execute(_DELETE_OTHERS, params)
        return written, cur.rowcount


async def list_all(conn: AsyncConnection[Any]) -> list[CompanyPredecessor]:
    async with conn.cursor(row_factory=class_row(CompanyPredecessor)) as cur:
        await cur.execute(_LIST)
        return await cur.fetchall()


async def predecessors_of(conn: AsyncConnection[Any], cik: int) -> list[int]:
    """The earlier CIKs whose filings are part of `cik`'s history."""
    async with conn.cursor() as cur:
        await cur.execute(_OF, {"cik": cik})
        return [int(row[0]) for row in await cur.fetchall()]


async def successors(conn: AsyncConnection[Any], ciks: Sequence[int]) -> dict[int, int]:
    """For each of these CIKs that is a predecessor, the company it became."""
    async with conn.cursor() as cur:
        await cur.execute(_SUCCESSORS, {"ciks": list(ciks)})
        return {int(predecessor): int(cik) for predecessor, cik in await cur.fetchall()}
