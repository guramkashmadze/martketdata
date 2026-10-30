"""/v1/changes: every change to a served value, with the reason and the value's proof now
(E5b-S2, PLAN.md §10.2, D-027)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.api.params import Cursor, Limit, moment
from marketdata.service.changes import ChangeFeed, changes_feed
from marketdata.service.pages import DEFAULT_LIMIT

router = APIRouter(prefix="/changes", tags=["Changes"])


@router.get(
    "",
    summary="Value changes",
    response_model=Envelope[ChangeFeed],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.CHANGES, "Changes to served values, newest first."),
        404: examples.NOT_FOUND,
    },
)
async def feed(
    conn: Conn,
    since: Annotated[
        str | None,
        Query(
            description=(
                "Made at or after this day (from its start, UTC) or time, e.g. `2026-10-01` "
                "or `2026-10-01T20:30:00Z`."
            )
        ),
    ] = None,
    ticker: Annotated[str | None, Query(description="Only this company's values.")] = None,
    reason: Annotated[
        Literal["correction", "restatement", "amendment"] | None,
        Query(
            description=(
                "`correction`: our rules, an override or the calc version changed; "
                "`restatement`: a later filing reports the period otherwise; `amendment`: the "
                "company's 8-K/A changed a release."
            )
        ),
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> EnvelopeResponse:
    """Every change to a stored earnings value or metric value (D-027), newest first: the old
    value, the new value, why, and the value as served now with its proof. A value is never
    changed silently; each change is also sent as a `data.changed` webhook event."""
    return respond(
        await changes_feed(
            conn,
            since=moment(since, "since"),
            ticker=ticker,
            reason=reason,
            limit=limit,
            cursor=cursor,
        )
    )
