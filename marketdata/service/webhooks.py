"""Webhook destinations, events and deliveries (PLAN.md §9.11; E5b-S1).

The operator manages destinations with `marketdata webhooks create|list|disable|enable|test`.
The worker publishes and delivers events through these functions (webhooks/), so the webhook
tables are read and written through the service layer, as the API reads the data.

- A destination belongs to a client, takes some event types and has a signing secret, which
  the caller generates, prints once and passes here encrypted.
- Publishing writes an event's body once and queues one delivery per active destination that
  takes its type (a test event: its one destination), behind live work and the watches.
- 50 deliveries in a row that failed every attempt switch a destination off, with when and why;
  any delivered event starts the count again.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid7

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import clients, earnings, filings, financial_metrics, tasks
from marketdata.db.repositories import webhooks as repository
from marketdata.db.repositories.webhooks import Delivery, Destination, Event, FailureCount
from marketdata.normalize.webhooks import (
    EARNINGS_VERIFIED,
    EVENT_TYPES,
    FILING_EVENT,
    FINANCIALS_UPDATED,
    HIGH_ITEMS,
    WEBHOOK_DELIVER,
    WEBHOOK_PUBLISH,
    check_url,
    delivery_key,
)
from marketdata.service.errors import InvalidParameter, NotFound

FAILURES_TO_DISABLE = 50
AUTO_DISABLED = f"{FAILURES_TO_DISABLE} deliveries in a row failed"
OPERATOR_DISABLED = "switched off by the operator"


def check_event_types(event_types: Sequence[str]) -> tuple[str, ...]:
    """The types asked for, each once, in §9.11's order; all of them when none is asked."""
    unknown = sorted(set(event_types) - set(EVENT_TYPES))
    if unknown:
        raise InvalidParameter(
            f"unknown event type {', '.join(unknown)}; the types are {', '.join(EVENT_TYPES)}"
        )
    return tuple(kind for kind in EVENT_TYPES if kind in event_types) or EVENT_TYPES


async def create_destination(
    conn: AsyncConnection[Any],
    client_name: str,
    url: str,
    event_types: Sequence[str],
    *,
    secret_enc: str,
    allow_loopback: bool = False,
) -> Destination:
    """A new destination for a client's endpoint. Raises InvalidParameter for a URL deliveries
    would refuse or an unknown event type, NotFound for an unknown client."""
    try:
        checked = check_url(url, allow_loopback=allow_loopback)
    except ValueError as exc:
        raise InvalidParameter(f"url: {exc}") from None
    types = check_event_types(event_types)
    found = await clients.clients(conn, client_name)
    if not found:
        raise NotFound(f"No client is named {client_name!r}. `marketdata clients list` shows them.")
    dest_id = await repository.insert_destination(
        conn, found[0].client_id, checked, secret_enc, types
    )
    return await get_destination(conn, dest_id)


async def get_destination(conn: AsyncConnection[Any], dest_id: int) -> Destination:
    found = await repository.destinations(conn, dest_id=dest_id)
    if not found:
        raise NotFound(f"No webhook destination has id {dest_id}. `marketdata webhooks list`.")
    return found[0]


async def list_destinations(
    conn: AsyncConnection[Any], client_name: str | None = None
) -> list[Destination]:
    if client_name is None:
        return await repository.destinations(conn)
    found = await clients.clients(conn, client_name)
    if not found:
        raise NotFound(f"No client is named {client_name!r}. `marketdata clients list` shows them.")
    return await repository.destinations(conn, client_id=found[0].client_id)


async def disable_destination(conn: AsyncConnection[Any], dest_id: int) -> Destination:
    """Switch a destination off: its queued deliveries are dropped. Twice changes nothing."""
    if not await repository.disable(conn, dest_id, OPERATOR_DISABLED):
        await get_destination(conn, dest_id)  # raises NotFound
    return await get_destination(conn, dest_id)


async def enable_destination(conn: AsyncConnection[Any], dest_id: int) -> Destination:
    """Switch a destination on again, its count of failed deliveries back at 0. Events
    published while it was off are not sent again."""
    if not await repository.enable(conn, dest_id):
        await get_destination(conn, dest_id)
    return await get_destination(conn, dest_id)


class TestEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    dest_id: int
    type: str
    subject: str


async def queue_test_event(
    conn: AsyncConnection[Any],
    dest_id: int,
    event_type: str | None = None,
    *,
    now: datetime | None = None,
) -> TestEvent:
    """A test event (`livemode: false`) for one destination, carrying the newest real object
    of its type; at most one a minute per destination (§9.11). The worker delivers it."""
    destination = await get_destination(conn, dest_id)
    if not destination.active:
        raise InvalidParameter(
            f"Destination {dest_id} is switched off ({destination.disabled_reason}); "
            f"`marketdata webhooks enable {dest_id}` first."
        )
    chosen = event_type or destination.event_types[0]
    if chosen not in destination.event_types:
        raise InvalidParameter(
            f"Destination {dest_id} takes {', '.join(destination.event_types)}, not {chosen}."
        )
    subject, detail = await _newest(conn, chosen)
    if not await repository.take_test_slot(conn, dest_id, now or datetime.now(UTC)):
        raise InvalidParameter(
            f"Destination {dest_id} had a test event in the last minute; one a minute is sent."
        )
    event_id = await repository.add_event(
        conn,
        uuid7(),
        chosen,
        subject,
        detail,
        livemode=False,
        client_id=destination.client_id,
        dest_id=dest_id,
    )
    if event_id is None:  # test events aren't unique; this can't happen
        raise RuntimeError(f"the test event for destination {dest_id} was not written")
    await tasks.enqueue(
        conn,
        WEBHOOK_PUBLISH,
        str(event_id),
        {"event_id": str(event_id)},
        priority=tasks.PRIORITY_LIVE,
    )
    return TestEvent(event_id=event_id, dest_id=dest_id, type=chosen, subject=subject)


async def _newest(conn: AsyncConnection[Any], event_type: str) -> tuple[str, dict[str, Any]]:
    """The subject (and detail) of the newest real data an event of this type could carry."""
    if event_type == EARNINGS_VERIFIED:
        releases = await earnings.verified_page(conn, limit=1)
        if releases:
            return releases[0].accession_no, {}
    elif event_type == FILING_EVENT:
        found = await filings.newest_with_items(conn, HIGH_ITEMS)
        if found is not None:
            return f"{found.accession_no}:{found.cik}", {}
    elif event_type == FINANCIALS_UPDATED:
        rows = await financial_metrics.rows_of_newest_filing(conn)
        if rows:
            cik, accession = rows[0].cik, rows[0].accession_no
            periods = sorted(
                {(row.period_type, row.period_end) for row in rows},
                key=lambda period: (period[1], period[0]),
                reverse=True,
            )
            return f"{cik}:test", {
                "cik": cik,
                "new": len(rows),
                "changed": 0,
                "metrics": sorted({row.metric for row in rows}),
                "periods": [
                    {"period_type": period_type, "period_end": end.isoformat()}
                    for period_type, end in periods
                ],
                "filings": [accession],
            }
    raise NotFound(f"No data is stored yet for a test event of type {event_type}.")


async def event_to_publish(conn: AsyncConnection[Any], event_id: UUID) -> Event:
    """The event, locked until the transaction ends so it is published once."""
    found = await repository.event(conn, event_id, for_update=True)
    if found is None:
        raise NotFound(f"No webhook event has id {event_id}.")
    return found


async def publish_event(conn: AsyncConnection[Any], event: Event, body: str) -> list[int]:
    """Write the event's body and queue a delivery to each destination that takes it; the
    destinations' ids (none when it was published already)."""
    if not await repository.publish(conn, event.event_id, body):
        return []
    destinations = await repository.subscribed(conn, event.type, event.dest_id)
    for dest_id in destinations:
        await tasks.enqueue(
            conn,
            WEBHOOK_DELIVER,
            delivery_key(event.event_id, dest_id),
            {"event_id": str(event.event_id), "dest_id": dest_id},
            priority=tasks.PRIORITY_DELIVERY,
        )
    return destinations


class DeliveryTarget(BaseModel):
    """What one delivery needs: where, the secret (encrypted) and the body as published."""

    model_config = ConfigDict(frozen=True)

    dest_id: int
    url: str
    secret_enc: str
    active: bool
    event_id: UUID
    event_type: str
    body: str


async def delivery_target(
    conn: AsyncConnection[Any], event_id: UUID, dest_id: int
) -> DeliveryTarget:
    destination = await get_destination(conn, dest_id)
    event = await repository.event(conn, event_id)
    if event is None or event.payload is None:
        raise NotFound(f"Webhook event {event_id} doesn't exist or isn't published.")
    return DeliveryTarget(
        dest_id=dest_id,
        url=destination.url,
        secret_enc=destination.secret_enc,
        active=destination.active,
        event_id=event_id,
        event_type=event.type,
        body=event.payload,
    )


async def record_attempt(
    conn: AsyncConnection[Any],
    event_id: UUID,
    dest_id: int,
    attempt: int,
    *,
    status_code: int | None,
    error: str | None,
    duration_ms: int,
    final: bool,
) -> FailureCount | None:
    """Log one attempt. A success starts the destination's count of failures again; a failed
    last attempt adds one, and the 50th in a row switches it off. The count, after a failed
    last attempt."""
    await repository.record_attempt(
        conn,
        event_id,
        dest_id,
        attempt,
        status_code=status_code,
        error=error,
        duration_ms=duration_ms,
    )
    if error is None:
        await repository.delivered(conn, dest_id)
        return None
    if not final:
        return None
    return await repository.delivery_failed(
        conn, dest_id, limit=FAILURES_TO_DISABLE, reason=AUTO_DISABLED
    )


async def deliveries(
    conn: AsyncConnection[Any], event_id: UUID, dest_id: int | None = None
) -> list[Delivery]:
    return await repository.deliveries(conn, event_id, dest_id)
