"""The frame_facts table (E4a-S5, D-085): one concept across filers from the SEC's frames API.

A load writes each frame's points; a point a later load gives differently (the SEC moved the
frame to a later filing) is updated in place. Points stay stored when a frame drops them.
"""

from collections.abc import Sequence
from typing import Any

from psycopg import AsyncConnection

from marketdata.normalize.public_float import Frame, FramePoint

_UPSERT = """
    insert into frame_facts (
      taxonomy, concept, unit, frame, cik, entity_name, period_start, period_end, value,
      accession_no, raw_key
    )
    select %(taxonomy)s, %(concept)s, %(unit)s, %(frame)s, * from unnest(
      %(cik)s::bigint[], %(entity_name)s::text[], %(period_start)s::date[],
      %(period_end)s::date[], %(value)s::numeric[], %(accession_no)s::text[], %(raw_key)s::text[]
    )
    on conflict (taxonomy, concept, unit, frame, cik) do update set
      entity_name = excluded.entity_name,
      period_start = excluded.period_start,
      period_end = excluded.period_end,
      value = excluded.value,
      accession_no = excluded.accession_no,
      raw_key = excluded.raw_key,
      loaded_at = now()
    where (frame_facts.period_start, frame_facts.period_end, frame_facts.value,
           frame_facts.accession_no)
      is distinct from (excluded.period_start, excluded.period_end, excluded.value,
                        excluded.accession_no)
"""

_POINTS = """
    select cik, entity_name, period_start, period_end, value, accession_no from frame_facts
    where taxonomy = %(taxonomy)s and concept = %(concept)s and unit = %(unit)s
"""


async def upsert(conn: AsyncConnection[Any], frame: Frame, raw_key: str) -> int:
    """Write a frame's points that are new or changed; returns how many."""
    if not frame.points:
        return 0
    points = frame.points
    params = {
        "taxonomy": frame.taxonomy,
        "concept": frame.concept,
        "unit": frame.unit,
        "frame": frame.frame,
        "cik": [point.cik for point in points],
        "entity_name": [point.entity_name for point in points],
        "period_start": [point.period_start for point in points],
        "period_end": [point.period_end for point in points],
        "value": [point.value for point in points],
        "accession_no": [point.accession_no for point in points],
        "raw_key": [raw_key] * len(points),
    }
    async with conn.cursor() as cur:
        await cur.execute(_UPSERT, params)
        return cur.rowcount


async def points(
    conn: AsyncConnection[Any], taxonomy: str, concept: str, unit: str
) -> Sequence[FramePoint]:
    """Every stored point of one concept and unit, over all frames."""
    params = {"taxonomy": taxonomy, "concept": concept, "unit": unit}
    async with conn.cursor() as cur:
        await cur.execute(_POINTS, params)
        return [FramePoint(*row) for row in await cur.fetchall()]
