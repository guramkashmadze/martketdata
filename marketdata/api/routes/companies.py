"""/v1/companies/{ticker}: snapshot, latest earnings release, financials and filings (E5a-S3,
PLAN.md §10.2). Each route calls one service function and returns its result in the envelope."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.api.params import Cursor, Limit, PeriodType, TickerPath, names
from marketdata.service.earnings import EarningsRelease, latest_earnings
from marketdata.service.filings import CompanyFilings, recent_filings
from marketdata.service.financials import (
    CORE_METRICS,
    DEFAULT_PERIODS,
    MAX_PERIODS,
    Financials,
    get_financials,
)
from marketdata.service.pages import DEFAULT_LIMIT
from marketdata.service.snapshot import CompanySnapshot, company_snapshot

router = APIRouter(prefix="/companies/{ticker}", tags=["Companies"])


@router.get(
    "",
    summary="Company snapshot",
    response_model=Envelope[CompanySnapshot],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.SNAPSHOT, "Identity, latest period, TTM, release and filings."),
        404: examples.NOT_FOUND,
        422: examples.NOT_APPLICABLE,
    },
)
async def snapshot(conn: Conn, ticker: TickerPath) -> EnvelopeResponse:
    """Identity and filer class, the latest quarter with metrics, the newest trailing-twelve-month
    value of each core metric (`not_reported` names those no filing reports), the latest
    earnings release and the three latest results, report, current-report and 5%-stake
    filings. Every value carries its proof."""
    return respond(await company_snapshot(conn, ticker))


@router.get(
    "/earnings/latest",
    summary="Latest earnings release",
    response_model=Envelope[EarningsRelease],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.EARNINGS_LATEST, "The latest release with each value's proof."),
        404: examples.NOT_FOUND,
        422: examples.NOT_APPLICABLE,
    },
)
async def earnings_latest(conn: Conn, ticker: TickerPath) -> EnvelopeResponse:
    """The company's newest earnings release (an 8-K with item 2.02), with revenue, operating
    income, net income and diluted EPS: each value with the row it was read from as printed,
    the checks it passed and its cross-check against the later 10-Q or 10-K. A release that
    failed verification is served too: its values are null with the reasons, and
    `release.status` says so."""
    return respond(await latest_earnings(conn, ticker))


@router.get(
    "/financials",
    summary="Financial metrics",
    response_model=Envelope[Financials],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.FINANCIALS, "Metric rows, each with its proof."),
        404: examples.NOT_FOUND,
        422: examples.NOT_APPLICABLE,
    },
)
async def financials(
    conn: Conn,
    ticker: TickerPath,
    metrics: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated metric names from the metric catalog; by default the core "
                "set: " + ", ".join(CORE_METRICS) + "."
            ),
            examples=["revenue,net_income,eps_diluted"],
        ),
    ] = None,
    period: Annotated[
        PeriodType,
        Query(description="Quarters (Q), fiscal years (FY) or trailing twelve months (TTM)."),
    ] = "Q",
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_PERIODS, description="Periods per metric, newest first (1 to 40)."),
    ] = DEFAULT_PERIODS,
) -> EnvelopeResponse:
    """The company's metric values, newest period first: reported by its 10-Qs and 10-Ks, or
    derived from them (a quarter from year-to-date values, the fourth quarter from the year,
    TTM from four quarters) and computed (margins, growth, free cash flow). Each row carries
    its proof; a value that doesn't apply or can't be derived is null with a quality flag."""
    return respond(
        await get_financials(conn, ticker, metrics=names(metrics), period=period, limit=limit)
    )


@router.get(
    "/filings",
    summary="Filings with event labels",
    response_model=Envelope[CompanyFilings],
    response_class=EnvelopeResponse,
    responses={
        **examples.ok(examples.FILINGS, "Filings, newest first, with plain-language events."),
        404: examples.NOT_FOUND,
    },
)
async def filings(
    conn: Conn,
    ticker: TickerPath,
    form: Annotated[
        str | None,
        Query(description="Only this form, e.g. `8-K`, `10-Q` or `SCHEDULE 13D`."),
    ] = None,
    since: Annotated[date | None, Query(description="Filed on or after this day.")] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> EnvelopeResponse:
    """The company's filings (its predecessors' too), newest first, each with its index page on
    sec.gov and its 8-K items and 5% stakes in plain words. Funds and foreign issuers have
    filings too."""
    return respond(
        await recent_filings(conn, ticker, form=form, since=since, limit=limit, cursor=cursor)
    )
