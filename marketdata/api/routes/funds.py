"""/v1/funds: the curated 13F managers and their portfolios (E9-S6, PLAN.md §10.2)."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.api.params import Cursor, Limit, names
from marketdata.service.funds import FundHoldings, FundList, fund_holdings, list_funds
from marketdata.service.pages import DEFAULT_LIMIT

router = APIRouter(prefix="/funds", tags=["Funds"])


@router.get(
    "",
    summary="Curated managers",
    response_model=Envelope[FundList],
    response_class=EnvelopeResponse,
    responses=examples.ok(examples.FUNDS, "The curated managers, each with its latest report."),
)
async def funds(
    conn: Conn,
    curated: Annotated[
        bool, Query(description="false also lists managers no longer curated.")
    ] = True,
) -> EnvelopeResponse:
    """The well-known investors whose 13F reports are loaded, by name, each with its latest
    report: the quarter end, the filing date and its positions' value in USD. A 13F lists long
    positions in US-listed securities at the quarter end and is filed up to 45 days later."""
    return respond(await list_funds(conn, curated=curated))


@router.get(
    "/{manager_cik}/holdings",
    summary="A manager's positions",
    response_model=Envelope[FundHoldings],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.FUND_HOLDINGS, "Positions, largest first, with their changes."),
        404: examples.NOT_FOUND,
    },
)
async def holdings(
    conn: Conn,
    manager_cik: Annotated[int, Path(description="The manager's CIK (`/v1/funds` lists them).")],
    quarter: Annotated[
        str | None,
        Query(
            description="The quarter, as 2026Q2 or 2026-06-30; by default the latest reported.",
            examples=["2026Q2"],
        ),
    ] = None,
    change: Annotated[
        str | None,
        Query(
            description="Comma-separated change types to keep: new, added, trimmed, unchanged, "
            "exited; by default all.",
            examples=["new,added"],
        ),
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> EnvelopeResponse:
    """A curated manager's positions at a quarter end, as its 13F report and amendments state
    them: lines merged per security and put or call, value in USD, weight among the long equity
    positions, and the change in shares from its report for the quarter before (new, added or
    trimmed by more than 1%, unchanged, exited), split-adjusted when a split happened. Held
    positions come largest value first, then those exited. Each position names the reports its
    lines come from; `filings` gives each report with its information table, the unit its
    values were read in and the summary page's line count and total."""
    return respond(
        await fund_holdings(
            conn, manager_cik, quarter=quarter, change=names(change), limit=limit, cursor=cursor
        )
    )
