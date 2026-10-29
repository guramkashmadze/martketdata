"""/v1/compare: 2-5 companies side by side (E5a-S3, PLAN.md §10.2)."""

from typing import Annotated

from fastapi import APIRouter, Query

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.api.params import PeriodType, names
from marketdata.service.compare import Comparison, compare

router = APIRouter(tags=["Companies"])


@router.get(
    "/compare",
    summary="Compare companies",
    response_model=Envelope[Comparison],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.COMPARE, "Each metric's newest value for each company."),
        404: examples.NOT_FOUND,
        422: examples.NOT_APPLICABLE,
    },
)
async def comparison(
    conn: Conn,
    tickers: Annotated[
        str, Query(description="2 to 5 comma-separated tickers.", examples=["AAPL,GOOGL"])
    ],
    metrics: Annotated[
        str | None, Query(description="Comma-separated metric names; by default the core set.")
    ] = None,
    period: Annotated[
        PeriodType,
        Query(
            description=(
                "TTM (the default) compares companies whose fiscal years end in different "
                "months; Q and FY give each company's newest quarter or year."
            )
        ),
    ] = "TTM",
) -> EnvelopeResponse:
    """Each metric's newest value for each company, with its own period and proof; null where
    none of a company's filings reports the metric. Values are shown side by side, never
    ranked."""
    return respond(await compare(conn, names(tickers) or [], metrics=names(metrics), period=period))
