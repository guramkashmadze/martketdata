"""/v1/earnings: the feed of verified releases and one release by accession number (E5a-S3,
PLAN.md §10.2)."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.api.params import Cursor, Limit, moment
from marketdata.service.earnings import EarningsFeed, EarningsRelease, earnings_feed, get_earnings
from marketdata.service.pages import DEFAULT_LIMIT

router = APIRouter(prefix="/earnings", tags=["Earnings"])


@router.get(
    "",
    summary="Earnings feed",
    response_model=Envelope[EarningsFeed],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.EARNINGS_FEED, "Verified releases, newest first."),
        404: examples.NOT_FOUND,
        422: examples.NOT_APPLICABLE,
    },
)
async def feed(
    conn: Conn,
    since: Annotated[
        str | None,
        Query(
            description=(
                "Accepted by the SEC at or after this day (from its start, UTC) or time, e.g. "
                "`2026-10-01` or `2026-10-01T20:30:00Z`."
            )
        ),
    ] = None,
    ticker: Annotated[str | None, Query(description="Only this company's releases.")] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> EnvelopeResponse:
    """Releases whose values passed verification (status `complete` or `partial`), newest first
    by SEC acceptance time, each with its proof; of every covered company, or one."""
    return respond(
        await earnings_feed(
            conn, since=moment(since, "since"), ticker=ticker, limit=limit, cursor=cursor
        )
    )


@router.get(
    "/{accession_no}",
    summary="One earnings release",
    response_model=Envelope[EarningsRelease],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.EARNINGS_LATEST, "The release with each value's proof."),
        404: examples.NOT_FOUND,
    },
)
async def one(
    conn: Conn,
    accession_no: Annotated[
        str,
        Path(
            description="The 8-K's accession number, with or without dashes.",
            examples=["0000320193-26-000018"],
        ),
    ],
) -> EnvelopeResponse:
    """One release, whatever its status, by the accession number of its 8-K."""
    return respond(await get_earnings(conn, accession_no))
