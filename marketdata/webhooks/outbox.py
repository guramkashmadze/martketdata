"""Publishing webhook events (E5b-S1, PLAN.md §9.11): the `webhook_publish` task.

An event was written in the transaction that changed the data (ingest/announce.py). Publishing
builds its body once, the §9.11 envelope around the object the API would serve now
(service/events.py), stores it and queues a delivery to each active destination that takes its
type, all in one transaction:

    {"id": "<event id>", "type": "earnings.verified", "created": "<when the data changed, UTC>",
     "livemode": true, "api_version": "v1",
     "data": {"object": {...}, "proof_url": "<PUBLIC_BASE_URL>/proof/earnings/<accession>"},
     "disclaimer": "Information from public filings. Not investment advice."}

Numbers keep every digit of their Decimal (D-096). `proof_url` is null without
PUBLIC_BASE_URL and for objects without one proof page.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from marketdata.config import Settings
from marketdata.normalize.webhooks import API_VERSION
from marketdata.service import webhooks
from marketdata.service.events import EventObject, event_object
from marketdata.service.json_text import dumps
from marketdata.service.served import DISCLAIMER

# A pool's `connection` method: a connection whose transaction commits on exit.
type Connect = Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]


async def publish(connect: Connect, settings: Settings, event_id: UUID) -> list[int]:
    """Build and store the event's body and queue its deliveries; the destinations' ids. An
    event published already changes nothing."""
    async with connect() as conn, conn.transaction():
        event = await webhooks.event_to_publish(conn, event_id)
        if event.published_at is not None:
            return []
        built = await event_object(conn, event.type, event.subject, event.detail)
        body = envelope(
            event.event_id, event.type, event.created_at, event.livemode, built, settings.base_url
        )
        return await webhooks.publish_event(conn, event, body)


def envelope(
    event_id: UUID,
    event_type: str,
    created: datetime,
    livemode: bool,
    built: EventObject,
    base_url: str | None,
) -> str:
    """The body every delivery of this event sends, as JSON text."""
    proof_url = base_url + built.proof_path if base_url and built.proof_path else None
    return dumps(
        {
            "id": str(event_id),
            "type": event_type,
            "created": created,
            "livemode": livemode,
            "api_version": API_VERSION,
            "data": {"object": built.object.model_dump(mode="python"), "proof_url": proof_url},
            "disclaimer": DISCLAIMER,
        }
    )
