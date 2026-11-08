"""How fresh the data is: the newest time in each pipeline table (E5b, /v1/status), and what an
answer's check time is read from (D-135): the watches of the companies and curated managers, and
a company's latest 10-K and 10-Q with whether their facts are stored."""

from collections.abc import Collection
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.freshness import PeriodicReport


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


class Watched(BaseModel):
    """A company or curated manager the scheduler watches, and its watch's last poll."""

    model_config = ConfigDict(frozen=True)

    cik: int
    checked_at: datetime | None  # null: never watched


_WATCHED = """
    select w.cik, s.checked_at
    from (select cik from companies where in_universe
          union
          select manager_cik from managers where curated) w
    left join watch_state s on s.cik = w.cik
    order by w.cik
"""

# A company's 10-Ks and 10-Qs (forms passed in), newest first, and whether xbrl_facts holds facts
# from each (D-135). A filing marked as not XBRL has no facts to wait for.
_PERIODIC_REPORTS = """
    select f.discovered_at,
           exists (select 1 from xbrl_facts x where x.accession_no = f.accession_no) as loaded
    from filings f
    where f.cik = %(cik)s and f.form = any(%(forms)s) and f.is_xbrl is not false
    order by f.filed_at desc, f.accession_no desc
    limit %(limit)s
"""


async def watched(conn: AsyncConnection[Any]) -> list[Watched]:
    """Every universe company and curated manager, with its watch's last poll."""
    async with conn.cursor(row_factory=class_row(Watched)) as cur:
        await cur.execute(_WATCHED)
        return await cur.fetchall()


async def periodic_reports(
    conn: AsyncConnection[Any], cik: int, forms: Collection[str], limit: int
) -> list[PeriodicReport]:
    """The company's latest `limit` filings of these forms on its own listing, newest first."""
    async with conn.cursor(row_factory=class_row(PeriodicReport)) as cur:
        await cur.execute(_PERIODIC_REPORTS, {"cik": cik, "forms": sorted(forms), "limit": limit})
        return await cur.fetchall()
