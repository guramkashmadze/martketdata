"""/v1/coverage: the covered tickers (E5a-S3, PLAN.md §10.2)."""

from fastapi import APIRouter

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.api.params import Cursor, Limit
from marketdata.service.companies import Coverage, coverage
from marketdata.service.pages import DEFAULT_LIMIT

router = APIRouter(tags=["Coverage"])


@router.get(
    "/coverage",
    summary="Covered tickers",
    response_model=Envelope[Coverage],
    response_class=EnvelopeResponse,
    responses=examples.ok(examples.COVERAGE, "Covered tickers with their class and data kinds."),
)
async def covered(
    conn: Conn, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> EnvelopeResponse:
    """Every current ticker of a covered company, by ticker, with its filer class and whether
    earnings and fundamentals apply, with the reason. Funds and foreign private issuers are
    listed with the reason their data doesn't apply."""
    return respond(await coverage(conn, limit=limit, cursor=cursor))
