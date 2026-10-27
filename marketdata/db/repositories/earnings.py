"""The earnings_releases and earnings_facts tables (PLAN.md §7, E7a-S4).

A release is written with all its facts in one statement each, keyed by accession number (and
slot): running a release again replaces its rows. `verified_at` keeps the first time a release
served a value, so latency is measured from the first verification. Value changes are written
by the caller in the same transaction (D-027).
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.changes import Served
from marketdata.normalize.earnings import ReleaseRecord


class StoredRelease(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: int
    accepted_at: datetime | None
    doc_name: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    period_end: date | None
    period_label: str | None
    status: str
    extractor: str
    extractor_version: str
    check_report: dict[str, Any]
    verified_at: datetime | None
    updated_at: datetime


class StoredFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    slot: str
    value: Decimal | None
    unit: str
    prior_value: Decimal | None
    change_pct: Decimal | None
    basis: str
    source_row_label: str | None
    source_quote: str | None
    scale_text: str | None
    method: str
    extractor_version: str
    check_status: str
    cross_check_status: str
    quality_flags: list[str]


_RELEASE_COLUMNS = """
    accession_no, cik, accepted_at, doc_name, fiscal_year, fiscal_period, period_end,
    period_label, status, extractor, extractor_version, check_report, verified_at, updated_at
"""
_FACT_COLUMNS = """
    accession_no, slot, value, unit, prior_value, change_pct, basis, source_row_label,
    source_quote, scale_text, method, extractor_version, check_status, cross_check_status,
    quality_flags
"""

_UPSERT_RELEASE = """
    insert into earnings_releases (
      accession_no, cik, accepted_at, doc_name, fiscal_year, fiscal_period, period_end,
      period_label, status, extractor, extractor_version, check_report, verified_at
    ) values (
      %(accession_no)s, %(cik)s, %(accepted_at)s, %(doc_name)s, %(fiscal_year)s,
      %(fiscal_period)s, %(period_end)s, %(period_label)s, %(status)s, %(extractor)s,
      %(extractor_version)s, %(check_report)s,
      case when %(status)s in ('complete', 'partial') then now() end
    )
    on conflict (accession_no) do update set
      cik = excluded.cik,
      accepted_at = excluded.accepted_at,
      doc_name = excluded.doc_name,
      fiscal_year = excluded.fiscal_year,
      fiscal_period = excluded.fiscal_period,
      period_end = excluded.period_end,
      period_label = excluded.period_label,
      status = excluded.status,
      extractor = excluded.extractor,
      extractor_version = excluded.extractor_version,
      check_report = excluded.check_report,
      verified_at = coalesce(earnings_releases.verified_at, excluded.verified_at),
      updated_at = now()
"""

# Cross-check columns belong to E4b and are left as they are.
_UPSERT_FACT = """
    insert into earnings_facts (
      accession_no, slot, value, unit, prior_value, change_pct, source_row_label, source_quote,
      scale_text, method, extractor_version, check_status, quality_flags
    ) values (
      %(accession_no)s, %(slot)s, %(value)s, %(unit)s, %(prior_value)s, %(change_pct)s,
      %(source_row_label)s, %(source_quote)s, %(scale_text)s, %(method)s,
      %(extractor_version)s, %(check_status)s, %(quality_flags)s
    )
    on conflict (accession_no, slot) do update set
      value = excluded.value,
      unit = excluded.unit,
      prior_value = excluded.prior_value,
      change_pct = excluded.change_pct,
      source_row_label = excluded.source_row_label,
      source_quote = excluded.source_quote,
      scale_text = excluded.scale_text,
      method = excluded.method,
      extractor_version = excluded.extractor_version,
      check_status = excluded.check_status,
      quality_flags = excluded.quality_flags
"""

_GET_RELEASE = f"select {_RELEASE_COLUMNS} from earnings_releases where accession_no = %(a)s"
_FACTS = f"select {_FACT_COLUMNS} from earnings_facts where accession_no = %(a)s order by slot"

# The release an 8-K/A amends: the latest other release of the company (or its predecessors)
# for the same quarter that served a value.
_AMENDED = """
    select accession_no from earnings_releases
    where cik = any(%(ciks)s) and period_end = %(period_end)s and accession_no <> %(accession)s
      and status in ('complete', 'partial')
    order by accepted_at desc nulls last, accession_no desc
    limit 1
"""

_LIST = f"""
    select {_RELEASE_COLUMNS} from earnings_releases
    where (%(ciks)s::bigint[] is null or cik = any(%(ciks)s))
    order by period_end desc nulls last, accepted_at desc nulls last, accession_no desc
    limit %(limit)s
"""

# A company's latest results release (its predecessors' too): an 8-K with item 2.02 that holds
# no statement is left out.
_LATEST = f"""
    select {_RELEASE_COLUMNS} from earnings_releases
    where cik = any(%(ciks)s) and status <> 'no_statement'
    order by accepted_at desc nulls last, accession_no desc
    limit 1
"""

_EVERY_RELEASE = f"""
    select {_RELEASE_COLUMNS} from earnings_releases order by cik, accepted_at, accession_no
"""
_EVERY_FACT = f"select {_FACT_COLUMNS} from earnings_facts order by accession_no, slot"

_ALL_FACTS = f"""
    select {_FACT_COLUMNS} from earnings_facts
    where accession_no = any(%(accessions)s)
    order by accession_no, slot
"""


async def store(conn: AsyncConnection[Any], release: ReleaseRecord) -> None:
    """Write a release and its facts, replacing what a run before wrote."""
    async with conn.cursor() as cur:
        params = {
            "accession_no": release.accession_no,
            "cik": release.cik,
            "accepted_at": release.accepted_at,
            "doc_name": release.doc_name,
            "fiscal_year": release.fiscal_year,
            "fiscal_period": release.fiscal_period,
            "period_end": release.period_end,
            "period_label": release.period_label,
            "status": release.status,
            "extractor": release.extractor,
            "extractor_version": release.extractor_version,
            "check_report": Jsonb(release.check_report),
        }
        await cur.execute(_UPSERT_RELEASE, params)
        for fact in release.facts:
            await cur.execute(
                _UPSERT_FACT,
                {
                    "accession_no": release.accession_no,
                    "slot": fact.slot,
                    "value": fact.value,
                    "unit": fact.unit,
                    "prior_value": fact.prior_value,
                    "change_pct": fact.change_pct,
                    "source_row_label": fact.source_row_label,
                    "source_quote": fact.source_quote,
                    "scale_text": fact.scale_text,
                    "method": fact.method,
                    "extractor_version": fact.extractor_version,
                    "check_status": fact.check_status,
                    "quality_flags": list(fact.quality_flags),
                },
            )


async def get_release(conn: AsyncConnection[Any], accession_no: str) -> StoredRelease | None:
    async with conn.cursor(row_factory=class_row(StoredRelease)) as cur:
        await cur.execute(_GET_RELEASE, {"a": accession_no})
        return await cur.fetchone()


async def facts(conn: AsyncConnection[Any], accession_no: str) -> list[StoredFact]:
    async with conn.cursor(row_factory=class_row(StoredFact)) as cur:
        await cur.execute(_FACTS, {"a": accession_no})
        return await cur.fetchall()


async def served(conn: AsyncConnection[Any], accession_no: str) -> list[Served]:
    """What each stored slot of a release serves (for value changes)."""
    return [
        Served(fact.slot, fact.value, fact.prior_value, fact.extractor_version)
        for fact in await facts(conn, accession_no)
    ]


async def amended_release(
    conn: AsyncConnection[Any], ciks: Sequence[int], period_end: date, accession_no: str
) -> str | None:
    """The stored release an 8-K/A for `period_end` amends, if there is one."""
    params = {"ciks": list(ciks), "period_end": period_end, "accession": accession_no}
    async with conn.cursor() as cur:
        await cur.execute(_AMENDED, params)
        row = await cur.fetchone()
    return str(row[0]) if row else None


async def list_releases(
    conn: AsyncConnection[Any], ciks: Sequence[int] | None = None, limit: int = 1000
) -> list[StoredRelease]:
    """Releases, newest quarter first."""
    params = {"ciks": list(ciks) if ciks is not None else None, "limit": limit}
    async with conn.cursor(row_factory=class_row(StoredRelease)) as cur:
        await cur.execute(_LIST, params)
        return await cur.fetchall()


async def facts_of(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredFact]:
    async with conn.cursor(row_factory=class_row(StoredFact)) as cur:
        await cur.execute(_ALL_FACTS, {"accessions": list(accessions)})
        return await cur.fetchall()


async def latest_release(conn: AsyncConnection[Any], ciks: Sequence[int]) -> StoredRelease | None:
    async with conn.cursor(row_factory=class_row(StoredRelease)) as cur:
        await cur.execute(_LATEST, {"ciks": list(ciks)})
        return await cur.fetchone()


async def every_release(conn: AsyncConnection[Any]) -> list[StoredRelease]:
    async with conn.cursor(row_factory=class_row(StoredRelease)) as cur:
        await cur.execute(_EVERY_RELEASE)
        return await cur.fetchall()


async def every_fact(conn: AsyncConnection[Any]) -> list[StoredFact]:
    async with conn.cursor(row_factory=class_row(StoredFact)) as cur:
        await cur.execute(_EVERY_FACT)
        return await cur.fetchall()
