"""Webhook events written where the data changes (E5b, PLAN.md §9.11).

`announce` runs inside the transaction that changes the data: it writes the event and queues
the `webhook_publish` task that builds its body and queues its deliveries, so an event exists
exactly when its change was committed. A live event is written once per type and subject: a
re-run announces nothing new.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid7

from psycopg import AsyncConnection

from marketdata.db.repositories import tasks, webhooks
from marketdata.normalize.webhooks import WEBHOOK_PUBLISH, EventType


async def announce(
    conn: AsyncConnection[Any],
    event_type: EventType,
    subject: str,
    detail: Mapping[str, Any] | None = None,
) -> UUID | None:
    """Write the event and queue its publishing; None when it was announced before."""
    event_id = await webhooks.add_event(conn, uuid7(), event_type, subject, detail or {})
    if event_id is not None:
        await publish_later(conn, event_id)
    return event_id


async def publish_later(conn: AsyncConnection[Any], event_id: UUID) -> None:
    """Queue the task that publishes this event, ahead of periodic work."""
    await tasks.enqueue(
        conn,
        WEBHOOK_PUBLISH,
        str(event_id),
        {"event_id": str(event_id)},
        priority=tasks.PRIORITY_LIVE,
    )
