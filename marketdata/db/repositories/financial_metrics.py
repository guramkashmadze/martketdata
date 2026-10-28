"""The financial_metrics table (PLAN.md §7, E4a-S4): metric rows built from XBRL facts.

A rebuild writes only rows that changed, so `updated_at` moves only with them; a value change
is logged in value_changes in the same transaction by the caller (D-027). Rows a rebuild no
longer gives are kept, never deleted silently.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.changes import StoredMetric
from marketdata.normalize.metrics import MetricKey, MetricRow


class StoredRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int
    metric: str
    period_type: str
    fiscal_year: int
    fiscal_period: str
    period_start: date | None
    period_end: date
    value: Decimal | None
    unit: str
    method: str
    concept: str | None
    accession_no: str | None
    source_accessions: list[str]
    filed_at: date | None
    quality_flags: list[str]
    calc_version: str
    updated_at: datetime


_COLUMNS = """
    cik, metric, period_type, fiscal_year, fiscal_period, period_start, period_end, value, unit,
    method, concept, accession_no, source_accessions, filed_at, quality_flags, calc_version,
    updated_at
"""

# Arrays of arrays don't unnest, so the two text arrays travel joined by newlines.
_UPSERT = """
    insert into financial_metrics (
      cik, metric, period_type, fiscal_year, fiscal_period, period_start, period_end, value,
      unit, method, concept, accession_no, source_accessions, filed_at, quality_flags,
      calc_version
    )
    select cik, metric, period_type, fiscal_year, fiscal_period, period_start, period_end,
           value, unit, method, concept, accession_no,
           coalesce(string_to_array(nullif(sources, ''), E'\\n'), '{}'),
           filed_at, coalesce(string_to_array(nullif(flags, ''), E'\\n'), '{}'), calc_version
    from unnest(
      %(cik)s::bigint[], %(metric)s::text[], %(period_type)s::text[], %(fiscal_year)s::int[],
      %(fiscal_period)s::text[], %(period_start)s::date[], %(period_end)s::date[],
      %(value)s::numeric[], %(unit)s::text[], %(method)s::text[], %(concept)s::text[],
      %(accession_no)s::text[], %(sources)s::text[], %(filed_at)s::date[], %(flags)s::text[],
      %(calc_version)s::text[]
    ) as rows (
      cik, metric, period_type, fiscal_year, fiscal_period, period_start, period_end, value,
      unit, method, concept, accession_no, sources, filed_at, flags, calc_version
    )
    on conflict (cik, metric, period_type, period_end) do update set
      fiscal_year = excluded.fiscal_year,
      fiscal_period = excluded.fiscal_period,
      period_start = excluded.period_start,
      value = excluded.value,
      unit = excluded.unit,
      method = excluded.method,
      concept = excluded.concept,
      accession_no = excluded.accession_no,
      source_accessions = excluded.source_accessions,
      filed_at = excluded.filed_at,
      quality_flags = excluded.quality_flags,
      calc_version = excluded.calc_version,
      updated_at = now()
    where (financial_metrics.fiscal_year, financial_metrics.fiscal_period,
           financial_metrics.period_start, financial_metrics.value, financial_metrics.unit,
           financial_metrics.method, financial_metrics.concept, financial_metrics.accession_no,
           financial_metrics.source_accessions, financial_metrics.filed_at,
           financial_metrics.quality_flags, financial_metrics.calc_version)
      is distinct from (excluded.fiscal_year, excluded.fiscal_period, excluded.period_start,
                        excluded.value, excluded.unit, excluded.method, excluded.concept,
                        excluded.accession_no, excluded.source_accessions, excluded.filed_at,
                        excluded.quality_flags, excluded.calc_version)
"""

_STORED = """
    select metric, period_type, period_end, value, accession_no, filed_at, calc_version
    from financial_metrics where cik = %(cik)s
"""

_SERIES = f"""
    select {_COLUMNS} from financial_metrics
    where cik = any(%(ciks)s) and metric = %(metric)s and period_type = %(period_type)s
    order by period_end desc, cik
"""

_COUNT = "select count(*) from financial_metrics where cik = %(cik)s"

# The quality report (E4b-S3): flags by company, metric and code, and each company's latest
# filing among its rows.
_FLAG_COUNTS = """
    select m.cik, m.metric, flag, count(*), max(m.period_end)
    from financial_metrics m cross join lateral unnest(m.quality_flags) as flag
    where %(ciks)s::bigint[] is null or m.cik = any(%(ciks)s)
    group by m.cik, m.metric, flag
    order by m.cik, m.metric, flag
"""

_ROW_COUNTS = """
    select cik, count(*), count(*) filter (where cardinality(quality_flags) > 0), max(filed_at)
    from financial_metrics
    where %(ciks)s::bigint[] is null or cik = any(%(ciks)s)
    group by cik
"""


async def upsert(conn: AsyncConnection[Any], rows: Sequence[MetricRow]) -> int:
    """Write the rows that are new or changed; returns how many."""
    if not rows:
        return 0
    params = {
        "cik": [row.cik for row in rows],
        "metric": [row.metric for row in rows],
        "period_type": [row.period_type for row in rows],
        "fiscal_year": [row.fiscal_year for row in rows],
        "fiscal_period": [row.fiscal_period for row in rows],
        "period_start": [row.period_start for row in rows],
        "period_end": [row.period_end for row in rows],
        "value": [row.value for row in rows],
        "unit": [row.unit for row in rows],
        "method": [row.method for row in rows],
        "concept": [row.concept for row in rows],
        "accession_no": [row.accession_no for row in rows],
        "sources": ["\n".join(row.source_accessions) for row in rows],
        "filed_at": [row.filed_at for row in rows],
        "flags": ["\n".join(row.quality_flags) for row in rows],
        "calc_version": [row.calc_version for row in rows],
    }
    async with conn.cursor() as cur:
        await cur.execute(_UPSERT, params)
        return cur.rowcount


async def stored(conn: AsyncConnection[Any], cik: int) -> dict[MetricKey, StoredMetric]:
    async with conn.cursor() as cur:
        await cur.execute(_STORED, {"cik": cik})
        return {
            (metric, period_type, period_end): StoredMetric(value, accession, filed, version)
            for metric, period_type, period_end, value, accession, filed, version in (
                await cur.fetchall()
            )
        }


async def series(
    conn: AsyncConnection[Any], ciks: Sequence[int], metric: str, period_type: str
) -> list[StoredRow]:
    """One metric's rows of these companies, newest period first."""
    params = {"ciks": list(ciks), "metric": metric, "period_type": period_type}
    async with conn.cursor(row_factory=class_row(StoredRow)) as cur:
        await cur.execute(_SERIES, params)
        return await cur.fetchall()


async def count(conn: AsyncConnection[Any], cik: int) -> int:
    async with conn.cursor() as cur:
        await cur.execute(_COUNT, {"cik": cik})
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def flag_counts(
    conn: AsyncConnection[Any], ciks: Sequence[int] | None = None
) -> list[tuple[int, str, str, int, date]]:
    """(cik, metric, flag, rows, latest period end) for every flag on these companies' rows."""
    async with conn.cursor() as cur:
        await cur.execute(_FLAG_COUNTS, {"ciks": list(ciks) if ciks is not None else None})
        return [
            (int(cik), str(metric), str(flag), int(rows), latest)
            for cik, metric, flag, rows, latest in await cur.fetchall()
        ]


async def row_counts(
    conn: AsyncConnection[Any], ciks: Sequence[int] | None = None
) -> dict[int, tuple[int, int, date | None]]:
    """For each company with rows: (rows, rows with a flag, the latest filing date)."""
    async with conn.cursor() as cur:
        await cur.execute(_ROW_COUNTS, {"ciks": list(ciks) if ciks is not None else None})
        return {
            int(cik): (int(rows), int(flagged), filed)
            for cik, rows, flagged, filed in await cur.fetchall()
        }
