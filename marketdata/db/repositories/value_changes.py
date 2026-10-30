"""The value_changes table (PLAN.md §7, D-027): every change to a served value, written in the
same transaction as the change itself. The change feed (E5b, GET /v1/changes) reads it newest
first; each change is announced as a `data.changed` webhook event by its id."""

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
    returning change_id
"""
_COLUMNS = "change_id, entity, entity_key, cik, old_value, new_value, reason, detail, changed_at"

_FOR_ACCESSION = f"""
    select {_COLUMNS} from value_changes
    where entity = 'earnings_fact' and entity_key->>'accession_no' = %(accession_no)s
    order by change_id
"""

# E5b: a release's history: changes to its own values and those an 8-K/A made to them.
_FOR_RELEASE = f"""
    select {_COLUMNS} from value_changes
    where entity = 'earnings_fact'
      and (entity_key->>'accession_no' = %(accession_no)s
           or entity_key->>'amends' = %(accession_no)s)
    order by change_id
"""

_FOR_METRIC = f"""
    select {_COLUMNS} from value_changes
    where entity = 'financial_metric' and cik = %(cik)s and entity_key->>'metric' = %(metric)s
    order by change_id
"""

# E5b: the feed, newest first, a page at a time before the last change served.
_PAGE = f"""
    select {_COLUMNS} from value_changes
    where (%(ciks)s::bigint[] is null or cik = any(%(ciks)s))
      and (%(since)s::timestamptz is null or changed_at >= %(since)s)
      and (%(reason)s::text is null or reason = %(reason)s)
      and (%(before)s::bigint is null or change_id < %(before)s)
    order by change_id desc
    limit %(limit)s
"""
_BY_ID = f"""
    select {_COLUMNS} from value_changes where change_id = any(%(ids)s) order by change_id
"""
_NEWEST = "select max(change_id) from value_changes"

_COUNT_BY_REASON = """
    select entity, reason, count(*) from value_changes
    where changed_at >= %(since)s
    group by entity, reason order by entity, reason
"""


async def record_fact_changes(
    conn: AsyncConnection[Any], cik: int, changes: Sequence[ValueChange]
) -> list[int]:
    """Log changes to earnings facts; their ids, in order."""
    return await _record(conn, "earnings_fact", cik, changes)


async def record_metric_changes(
    conn: AsyncConnection[Any], cik: int, changes: Sequence[ValueChange]
) -> list[int]:
    """Log changes to metric values; their ids, in order."""
    return await _record(conn, "financial_metric", cik, changes)


async def _record(
    conn: AsyncConnection[Any], entity: str, cik: int, changes: Sequence[ValueChange]
) -> list[int]:
    ids = []
    async with conn.cursor() as cur:
        for change in changes:
            await cur.execute(
                _INSERT,
                {
                    "entity": entity,
                    "entity_key": Jsonb(dict(change.entity_key)),
                    "cik": cik,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                    "reason": change.reason,
                    "detail": change.detail,
                },
            )
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError(f"no value change was logged for CIK {cik}")
            ids.append(int(row[0]))
    return ids


async def for_metric(conn: AsyncConnection[Any], cik: int, metric: str) -> list[StoredChange]:
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_FOR_METRIC, {"cik": cik, "metric": metric})
        return await cur.fetchall()


async def for_accession(conn: AsyncConnection[Any], accession_no: str) -> list[StoredChange]:
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_FOR_ACCESSION, {"accession_no": accession_no})
        return await cur.fetchall()


async def for_release(conn: AsyncConnection[Any], accession_no: str) -> list[StoredChange]:
    """A release's change history: its own values' and those an 8-K/A changed."""
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_FOR_RELEASE, {"accession_no": accession_no})
        return await cur.fetchall()


async def page(
    conn: AsyncConnection[Any],
    ciks: Sequence[int] | None = None,
    *,
    since: datetime | None = None,
    reason: str | None = None,
    before: int | None = None,
    limit: int = 20,
) -> list[StoredChange]:
    """Changes (of these companies, or all), newest first: made at or after `since`, for
    `reason`, older than the change `before`."""
    params = {
        "ciks": list(ciks) if ciks is not None else None,
        "since": since,
        "reason": reason,
        "before": before,
        "limit": limit,
    }
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_PAGE, params)
        return await cur.fetchall()


async def by_id(conn: AsyncConnection[Any], change_ids: Sequence[int]) -> list[StoredChange]:
    async with conn.cursor(row_factory=class_row(StoredChange)) as cur:
        await cur.execute(_BY_ID, {"ids": list(change_ids)})
        return await cur.fetchall()


async def newest(conn: AsyncConnection[Any]) -> int | None:
    """The id of the latest change, or None."""
    async with conn.cursor() as cur:
        await cur.execute(_NEWEST)
        row = await cur.fetchone()
    return None if row is None or row[0] is None else int(row[0])


async def count_by_reason(
    conn: AsyncConnection[Any], since: datetime
) -> dict[tuple[str, str], int]:
    """Changes made at or after `since`, by entity and reason."""
    async with conn.cursor() as cur:
        await cur.execute(_COUNT_BY_REASON, {"since": since})
        return {
            (str(entity), str(reason)): int(count) for entity, reason, count in await cur.fetchall()
        }
