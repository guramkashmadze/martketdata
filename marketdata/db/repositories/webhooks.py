"""The webhook tables (PLAN.md §7, §9.11; E5b): destinations, events and delivery attempts.

An event is written by the code that changes the data, in the same transaction (an outbox):
once per type and subject for live events, so a re-run writes nothing new. Publishing writes its
body once; every attempt sends those bytes. A destination counts the deliveries that failed in a
row and is switched off at the limit, keeping when and why.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict


class Destination(BaseModel):
    model_config = ConfigDict(frozen=True)

    dest_id: int
    client_id: int
    client_name: str
    url: str
    secret_enc: str
    event_types: list[str]
    active: bool
    consecutive_failures: int
    created_at: datetime
    disabled_at: datetime | None
    disabled_reason: str | None
    last_test_at: datetime | None
    last_attempt_at: datetime | None


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    type: str
    subject: str
    detail: dict[str, Any]
    client_id: int | None
    dest_id: int | None  # a test event's one destination
    livemode: bool
    payload: str | None  # the body as delivered; null until published
    created_at: datetime
    published_at: datetime | None


class Delivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    dest_id: int
    attempt: int
    status_code: int | None
    error: str | None
    duration_ms: int
    attempted_at: datetime


class FailureCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    consecutive_failures: int
    active: bool


_INSERT_DESTINATION = """
    insert into webhook_destinations (client_id, url, secret_enc, event_types)
    values (%(client_id)s, %(url)s, %(secret_enc)s, %(event_types)s)
    returning dest_id
"""
_DESTINATIONS = """
    select d.dest_id, d.client_id, c.name as client_name, d.url, d.secret_enc, d.event_types,
           d.active, d.consecutive_failures, d.created_at, d.disabled_at, d.disabled_reason,
           d.last_test_at,
           (select max(w.attempted_at) from webhook_deliveries w
            where w.dest_id = d.dest_id) as last_attempt_at
    from webhook_destinations d join clients c using (client_id)
    where (%(client_id)s::bigint is null or d.client_id = %(client_id)s)
      and (%(dest_id)s::bigint is null or d.dest_id = %(dest_id)s)
    order by d.dest_id
"""
# Switched off twice, a destination keeps the first time and reason.
_DISABLE = """
    update webhook_destinations set
      active = false,
      disabled_at = coalesce(disabled_at, now()),
      disabled_reason = coalesce(disabled_reason, %(reason)s)
    where dest_id = %(dest_id)s
"""
_ENABLE = """
    update webhook_destinations set
      active = true, disabled_at = null, disabled_reason = null, consecutive_failures = 0
    where dest_id = %(dest_id)s
"""
# One test event a minute per destination (§9.11): the slot is taken atomically.
_TEST_SLOT = """
    update webhook_destinations set last_test_at = %(now)s
    where dest_id = %(dest_id)s
      and (last_test_at is null or last_test_at <= %(now)s - interval '1 minute')
    returning dest_id
"""
_ADD_EVENT = """
    insert into webhook_events (event_id, type, subject, detail, client_id, dest_id, livemode)
    values (%(event_id)s, %(type)s, %(subject)s, %(detail)s, %(client_id)s, %(dest_id)s,
            %(livemode)s)
    on conflict (type, subject) where livemode do nothing
    returning event_id
"""
_EVENT_COLUMNS = """
    event_id, type, subject, detail, client_id, dest_id, livemode, payload::text as payload,
    created_at, published_at
"""
_EVENT = f"select {_EVENT_COLUMNS} from webhook_events where event_id = %(event_id)s"
_EVENT_FOR_UPDATE = _EVENT + " for update"
_PUBLISH = """
    update webhook_events set payload = %(payload)s::json, published_at = now()
    where event_id = %(event_id)s and published_at is null
"""
_SUBSCRIBED = """
    select dest_id from webhook_destinations
    where active and %(type)s = any(event_types)
      and (%(dest_id)s::bigint is null or dest_id = %(dest_id)s)
    order by dest_id
"""
# A re-claimed attempt (its worker died) writes over the row of the attempt it repeats.
_RECORD = """
    insert into webhook_deliveries (event_id, dest_id, attempt, status_code, error, duration_ms)
    values (%(event_id)s, %(dest_id)s, %(attempt)s, %(status_code)s, %(error)s, %(duration_ms)s)
    on conflict (event_id, dest_id, attempt) do update set
      status_code = excluded.status_code,
      error = excluded.error,
      duration_ms = excluded.duration_ms,
      attempted_at = now()
"""
_SUCCEEDED = "update webhook_destinations set consecutive_failures = 0 where dest_id = %(dest_id)s"
# On the right of `set`, columns are the row before the update.
_FAILED = """
    update webhook_destinations set
      consecutive_failures = consecutive_failures + 1,
      active = active and consecutive_failures + 1 < %(limit)s,
      disabled_at = case
        when active and consecutive_failures + 1 >= %(limit)s then now() else disabled_at end,
      disabled_reason = case
        when active and consecutive_failures + 1 >= %(limit)s then %(reason)s
        else disabled_reason end
    where dest_id = %(dest_id)s
    returning consecutive_failures, active
"""
_DELIVERIES = """
    select event_id, dest_id, attempt, status_code, error, duration_ms, attempted_at
    from webhook_deliveries
    where event_id = %(event_id)s and (%(dest_id)s::bigint is null or dest_id = %(dest_id)s)
    order by dest_id, attempt
"""


async def insert_destination(
    conn: AsyncConnection[Any],
    client_id: int,
    url: str,
    secret_enc: str,
    event_types: Sequence[str],
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            _INSERT_DESTINATION,
            {
                "client_id": client_id,
                "url": url,
                "secret_enc": secret_enc,
                "event_types": list(event_types),
            },
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"no destination was inserted for client {client_id}")
    return int(row[0])


async def destinations(
    conn: AsyncConnection[Any], *, client_id: int | None = None, dest_id: int | None = None
) -> list[Destination]:
    """Destinations by id, of one client or all, each with its latest delivery attempt."""
    async with conn.cursor(row_factory=class_row(Destination)) as cur:
        await cur.execute(_DESTINATIONS, {"client_id": client_id, "dest_id": dest_id})
        return await cur.fetchall()


async def disable(conn: AsyncConnection[Any], dest_id: int, reason: str) -> bool:
    """Switch a destination off; False when no destination has this id."""
    async with conn.cursor() as cur:
        await cur.execute(_DISABLE, {"dest_id": dest_id, "reason": reason})
        return cur.rowcount > 0


async def enable(conn: AsyncConnection[Any], dest_id: int) -> bool:
    """Switch a destination on with its failure count reset; False when there is none."""
    async with conn.cursor() as cur:
        await cur.execute(_ENABLE, {"dest_id": dest_id})
        return cur.rowcount > 0


async def take_test_slot(conn: AsyncConnection[Any], dest_id: int, now: datetime) -> bool:
    """Note a test event now; False when the destination had one in the minute before."""
    async with conn.cursor() as cur:
        await cur.execute(_TEST_SLOT, {"dest_id": dest_id, "now": now})
        return await cur.fetchone() is not None


async def add_event(
    conn: AsyncConnection[Any],
    event_id: UUID,
    event_type: str,
    subject: str,
    detail: Mapping[str, Any],
    *,
    livemode: bool = True,
    client_id: int | None = None,
    dest_id: int | None = None,
) -> UUID | None:
    """Write an event to publish; None when a live event of this type and subject exists."""
    async with conn.cursor() as cur:
        await cur.execute(
            _ADD_EVENT,
            {
                "event_id": event_id,
                "type": event_type,
                "subject": subject,
                "detail": Jsonb(dict(detail)),
                "client_id": client_id,
                "dest_id": dest_id,
                "livemode": livemode,
            },
        )
        row = await cur.fetchone()
    return None if row is None else UUID(str(row[0]))


async def event(
    conn: AsyncConnection[Any], event_id: UUID, *, for_update: bool = False
) -> Event | None:
    async with conn.cursor(row_factory=class_row(Event)) as cur:
        await cur.execute(_EVENT_FOR_UPDATE if for_update else _EVENT, {"event_id": event_id})
        return await cur.fetchone()


async def publish(conn: AsyncConnection[Any], event_id: UUID, payload: str) -> bool:
    """Write an event's body; False when it was published already."""
    async with conn.cursor() as cur:
        await cur.execute(_PUBLISH, {"event_id": event_id, "payload": payload})
        return cur.rowcount > 0


async def subscribed(
    conn: AsyncConnection[Any], event_type: str, dest_id: int | None = None
) -> list[int]:
    """Active destinations that take this type of event (only `dest_id`, when given)."""
    async with conn.cursor() as cur:
        await cur.execute(_SUBSCRIBED, {"type": event_type, "dest_id": dest_id})
        return [int(row[0]) for row in await cur.fetchall()]


async def record_attempt(
    conn: AsyncConnection[Any],
    event_id: UUID,
    dest_id: int,
    attempt: int,
    *,
    status_code: int | None,
    error: str | None,
    duration_ms: int,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            _RECORD,
            {
                "event_id": event_id,
                "dest_id": dest_id,
                "attempt": attempt,
                "status_code": status_code,
                "error": error,
                "duration_ms": duration_ms,
            },
        )


async def delivered(conn: AsyncConnection[Any], dest_id: int) -> None:
    """A delivery succeeded: the count of failures in a row starts again."""
    async with conn.cursor() as cur:
        await cur.execute(_SUCCEEDED, {"dest_id": dest_id})


async def delivery_failed(
    conn: AsyncConnection[Any], dest_id: int, *, limit: int, reason: str
) -> FailureCount | None:
    """Count a failed delivery; at `limit` in a row the destination is switched off."""
    async with conn.cursor(row_factory=class_row(FailureCount)) as cur:
        await cur.execute(_FAILED, {"dest_id": dest_id, "limit": limit, "reason": reason})
        return await cur.fetchone()


async def deliveries(
    conn: AsyncConnection[Any], event_id: UUID, dest_id: int | None = None
) -> list[Delivery]:
    async with conn.cursor(row_factory=class_row(Delivery)) as cur:
        await cur.execute(_DELIVERIES, {"event_id": event_id, "dest_id": dest_id})
        return await cur.fetchall()
