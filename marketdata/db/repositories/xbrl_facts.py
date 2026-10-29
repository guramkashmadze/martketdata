"""The xbrl_facts table (PLAN.md §7, E4a-S1): every us-gaap and dei fact of a loaded company.

A fact is keyed by company, concept, unit, period and accession number, so its value never
moves from one filing to another. A refresh inserts the facts not stored yet and updates a
stored fact whose details the SEC changed (its `frame` moves to the latest filing that reports
the period); facts a file no longer lists stay stored.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection

from marketdata.normalize.companyfacts import XbrlFact

BATCH = 5000  # rows per statement

# PostgreSQL 18's RETURNING old and new (D-040): `old` is null for an inserted row.
_UPSERT = """
    insert into xbrl_facts (
      cik, taxonomy, concept, unit, period_start, period_end, value, fy, fp, form, frame,
      filed_at, accession_no
    )
    select * from unnest(
      %(cik)s::bigint[], %(taxonomy)s::text[], %(concept)s::text[], %(unit)s::text[],
      %(period_start)s::date[], %(period_end)s::date[], %(value)s::numeric[], %(fy)s::int[],
      %(fp)s::text[], %(form)s::text[], %(frame)s::text[], %(filed_at)s::date[],
      %(accession_no)s::text[]
    )
    on conflict (cik, taxonomy, concept, unit, period_start, period_end, accession_no)
    do update set
      value = excluded.value,
      fy = excluded.fy,
      fp = excluded.fp,
      form = excluded.form,
      frame = excluded.frame,
      filed_at = excluded.filed_at
    where (xbrl_facts.value, xbrl_facts.fy, xbrl_facts.fp, xbrl_facts.form, xbrl_facts.frame,
           xbrl_facts.filed_at)
      is distinct from (excluded.value, excluded.fy, excluded.fp, excluded.form,
                        excluded.frame, excluded.filed_at)
    returning old.id is null, old.value, new.value, new.taxonomy, new.concept, new.unit,
              new.period_start, new.period_end, new.accession_no
"""

_COUNT = "select count(*) from xbrl_facts where cik = %(cik)s"

_CIKS = "select distinct cik from xbrl_facts order by cik"

# E5a: the form and filing date of filings known from their facts.
_FILINGS = """
    select distinct on (accession_no) accession_no, cik, form, filed_at
    from xbrl_facts where accession_no = any(%(accessions)s)
    order by accession_no, cik
"""

_FOR_CONCEPTS = """
    select cik, taxonomy, concept, unit, period_start, period_end, value, fy, fp, form, frame,
           filed_at, accession_no
    from xbrl_facts
    where cik = %(cik)s
      and ((taxonomy = 'us-gaap' and concept = any(%(gaap)s))
           or (taxonomy = 'dei' and concept = any(%(dei)s)))
"""


@dataclass(frozen=True)
class ValueMoved:
    """A stored fact whose value the SEC's file now gives differently, for the same filing."""

    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    accession_no: str
    old_value: Decimal
    new_value: Decimal


@dataclass(frozen=True)
class FactsWritten:
    inserted: int
    updated: int  # stored facts whose details changed
    moved: tuple[ValueMoved, ...]  # of those, the ones whose value changed


async def upsert(conn: AsyncConnection[Any], facts: Sequence[XbrlFact]) -> FactsWritten:
    inserted = updated = 0
    moved: list[ValueMoved] = []
    async with conn.cursor() as cur:
        for start in range(0, len(facts), BATCH):
            batch = facts[start : start + BATCH]
            await cur.execute(_UPSERT, _params(batch))
            for row in await cur.fetchall():
                was_new, old_value, new_value, *where = row
                if was_new:
                    inserted += 1
                    continue
                updated += 1
                if old_value != new_value:
                    taxonomy, concept, unit, period_start, period_end, accession = where
                    moved.append(
                        ValueMoved(
                            taxonomy,
                            concept,
                            unit,
                            period_start,
                            period_end,
                            accession,
                            old_value,
                            new_value,
                        )
                    )
    return FactsWritten(inserted, updated, tuple(moved))


async def for_concepts(
    conn: AsyncConnection[Any], cik: int, concepts: Mapping[str, Sequence[str]]
) -> list[XbrlFact]:
    """A company's stored facts of these concepts (by taxonomy), in every form and unit."""
    params = {
        "cik": cik,
        "gaap": list(concepts.get("us-gaap", ())),
        "dei": list(concepts.get("dei", ())),
    }
    async with conn.cursor() as cur:
        await cur.execute(_FOR_CONCEPTS, params)
        return [XbrlFact(*row) for row in await cur.fetchall()]


async def ciks(conn: AsyncConnection[Any]) -> list[int]:
    """Every company with stored facts."""
    async with conn.cursor() as cur:
        await cur.execute(_CIKS)
        return [int(row[0]) for row in await cur.fetchall()]


async def count(conn: AsyncConnection[Any], cik: int) -> int:
    async with conn.cursor() as cur:
        await cur.execute(_COUNT, {"cik": cik})
        row = await cur.fetchone()
    return int(row[0]) if row else 0


def _params(facts: Sequence[XbrlFact]) -> dict[str, list[Any]]:
    return {
        "cik": [fact.cik for fact in facts],
        "taxonomy": [fact.taxonomy for fact in facts],
        "concept": [fact.concept for fact in facts],
        "unit": [fact.unit for fact in facts],
        "period_start": [fact.period_start for fact in facts],
        "period_end": [fact.period_end for fact in facts],
        "value": [fact.value for fact in facts],
        "fy": [fact.fy for fact in facts],
        "fp": [fact.fp for fact in facts],
        "form": [fact.form for fact in facts],
        "frame": [fact.frame for fact in facts],
        "filed_at": [fact.filed_at for fact in facts],
        "accession_no": [fact.accession_no for fact in facts],
    }


@dataclass(frozen=True)
class FactFiling:
    accession_no: str
    cik: int
    form: str | None
    filed_at: date


async def filings_of_facts(
    conn: AsyncConnection[Any], accessions: Sequence[str]
) -> list[FactFiling]:
    """The filing each of these accession numbers names in the stored facts, by accession."""
    async with conn.cursor() as cur:
        await cur.execute(_FILINGS, {"accessions": list(accessions)})
        return [
            FactFiling(str(accession), int(cik), form, filed)
            for accession, cik, form, filed in await cur.fetchall()
        ]
