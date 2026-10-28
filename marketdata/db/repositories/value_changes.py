"""The value_changes table (PLAN.md §7, D-027): every change to a served value, written in the
same transaction as the change itself."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.changes import ValueChange


class StoredChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    change_id: int
    entity: str
    entity_key: dict[str, Any]
    cik: int
    old_value: Decimal | None
    new_value: Decimal | None
    reason: str
    detail: str | None
    changed_at: datetime


_INSERT = """
    insert into value_changes (entity, entity_key, cik, old_value, new_value, reason, detail)
    values (%(entity)s, %(entity_key)s, %(cik)s, %(old_value)s, %(new_value)s, %(reason)s,
            %(detail)s)
"""

_FOR_ACCESSION = """
    select change_id, entity, entity_key, cik, old_value, new_value, reason, detail, changed_at
    from value_changes
    where entity = 'earnings_fact' and entity_key->>'accession_no' = %(accession_no)s
    order by change_id
"""

_FOR_METRIC = """
    select change_id, entity, entity_key, cik, old_value, new_value, reason, detail, changed_at
    from value_changes
    where entity = 'financial_metric' and cik = %(cik)s and entity_key->>'metric' = %(metric)s
    order by change_id
"""

_COUNT_BY_REASON = """
    select reason, count(*) from value_changes
    where entity = 'earnings_fact' and changed_at >= %(since)s
    group by reason order by reason
"""


async def record_fact_changes(
    conn: AsyncConnection[Any], cik: int, changes: Sequence[ValueChange]
) -> None:
    async with conn.cursor() as cur:
        for change in changes:
            await cur.execute(
                _INSERT,
                {
                    "entity": "earnings_fact",
                    "entity_key": Jsonb(dict(change.entity_key)),
                    "cik": cik,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                    "reason": change.reason,
                    "detail": change.detail,
                },
            )


async def record_metric_changes(
    conn: AsyncConnection[Any], cik: int, changes: Sequence[ValueChange]
) -> None:
    async with conn.cursor() as cur:
        for change in changes:
            await cur.execute(
                _INSERT,
                {
                    "entity": "financial_metric",
                    "entity_key": Jsonb(dict(change.entity_key)),
                    "cik": cik,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                    "reason": change.reason,
                    "detail": change.detail,
                },
            )


async def for_metric(conn: AsyncConnection[Any], cik: int, metric: str) -> list[StoredChange]:
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_FOR_METRIC, {"cik": cik, "metric": metric})
        return await cur.fetchall()


async def for_accession(conn: AsyncConnection[Any], accession_no: str) -> list[StoredChange]:
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_FOR_ACCESSION, {"accession_no": accession_no})
        return await cur.fetchall()


async def count_by_reason(conn: AsyncConnection[Any], since: datetime) -> dict[str, int]:
    async with conn.cursor() as cur:
        await cur.execute(_COUNT_BY_REASON, {"since": since})
        return {str(reason): int(count) for reason, count in await cur.fetchall()}
