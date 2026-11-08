"""The search and the company page (E15-S2, PLAN.md §9.14).

A company page shows the company's identity and class, what applies to it and why, its latest
earnings release with the changes from a year earlier, eight quarters of the core metrics with a
chart, its recent filings with their plain-language labels and when the SEC was last checked for
them (D-135), its insiders' trades (E8, D-111) and the curated managers holding it (E9, D-120).
Every number links to its proof page. A fund, a foreign issuer or a company outside the universe
gets the same page with the parts that apply and the reasons the others don't, never an error.
Prices come with E10.

Everything is read through the service functions the API and the MCP server use.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from marketdata.api.deps import Conn
from marketdata.normalize.metric_catalog import UNITS
from marketdata.normalize.tickers import TickerError, canonical_ticker
from marketdata.service.companies import (
    CompanyMatch,
    CoveredTicker,
    company_profile,
    coverage,
    lookup,
    resolve_ticker,
)
from marketdata.service.errors import InvalidParameter
from marketdata.service.filings import recent_filings
from marketdata.service.financials import CORE_METRICS, MetricView, get_financials
from marketdata.service.funds import company_funds
from marketdata.service.insiders import insider_activity
from marketdata.service.pages import MAX_LIMIT
from marketdata.service.snapshot import company_snapshot
from marketdata.web.access import require_ui_access
from marketdata.web.render import METRIC_NAMES, metric_url, page, scale_name, scaled

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_ui_access)])

HISTORY_QUARTERS = 8
FILINGS_SHOWN = 10
INSIDER_TRADES = 20  # a page of trades; more load on request
INSIDER_WINDOWS = (90, 365)
# The metrics the history chart offers, of the core set.
CHARTED = (
    "revenue",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "free_cash_flow",
)
# Security types the home page lists a company by: its shares or fund units, not its preferred
# shares, warrants or units.
LISTED_TYPES = ("common", "fund", "adr")
CLASS_ORDER = ("domestic", "foreign_private_issuer", "fund", "other", "unknown")


@dataclass(frozen=True)
class Column:
    period_end: date
    label: str  # the company's fiscal quarter: Q2 2026


@dataclass(frozen=True)
class HistoryRow:
    metric: str
    unit: str
    cells: tuple[MetricView | None, ...]  # by column; none when no row is stored


def history(
    rows: Sequence[MetricView], metrics: Sequence[str], quarters: int = HISTORY_QUARTERS
) -> tuple[tuple[Column, ...], tuple[HistoryRow, ...]]:
    """The latest `quarters` periods of these metrics as a table, oldest first; a metric with no
    row in any of them is left out."""
    named: dict[date, MetricView] = {}
    for row in rows:
        named.setdefault(row.period_end, row)
    ends = sorted(named)[-quarters:]
    columns = tuple(
        Column(end, f"{named[end].fiscal_period} {named[end].fiscal_year}") for end in ends
    )
    found = {(row.metric, row.period_end): row for row in rows}
    table = []
    for metric in metrics:
        cells = tuple(found.get((metric, end)) for end in ends)
        if any(cell is not None for cell in cells):
            table.append(HistoryRow(metric, UNITS[metric], cells))
    return columns, tuple(table)


def chart_data(ticker: str, columns: Sequence[Column], table: Sequence[HistoryRow]) -> Any:
    """What the history chart draws (static/app.js): each charted metric's values exactly in the
    table's scale, as text, with their proof pages; a bar links to its value's proof."""
    series = []
    for row in table:
        if row.metric not in CHARTED or not any(c and c.value is not None for c in row.cells):
            continue
        series.append(
            {
                "metric": row.metric,
                "name": METRIC_NAMES[row.metric],
                "unit": scale_name(row.unit),
                "values": [
                    (
                        scaled(cell.value, row.unit).replace(",", "").removesuffix("%")
                        if cell is not None and cell.value is not None
                        else None
                    )
                    for cell in row.cells
                ],
                "text": [
                    scaled(cell.value if cell is not None else None, row.unit) for cell in row.cells
                ],
                "proof": [metric_url(ticker, cell) if cell else None for cell in row.cells],
            }
        )
    return {"labels": [column.label for column in columns], "series": series}


@dataclass(frozen=True)
class Listed:
    """A covered company as the home page lists it."""

    cik: int
    name: str | None
    filer_type: str
    tickers: tuple[str, ...]


async def covered(conn: Conn) -> dict[str, list[Listed]]:
    """The universe's companies by class, each with its share or fund tickers."""
    found: list[CoveredTicker] = []
    cursor: str | None = None
    while True:
        listed = (await coverage(conn, limit=MAX_LIMIT, cursor=cursor)).data
        found.extend(listed.tickers)
        cursor = listed.next_cursor
        if cursor is None:
            break
    tickers: dict[int, list[CoveredTicker]] = {}
    for each in found:
        if each.security_type in LISTED_TYPES:
            tickers.setdefault(each.cik, []).append(each)
    companies = sorted(
        (
            Listed(rows[0].cik, rows[0].name, rows[0].filer_type, tuple(r.ticker for r in rows))
            for rows in tickers.values()
        ),
        key=lambda company: company.tickers[0],
    )
    return {
        kind: [company for company in companies if company.filer_type == kind]
        for kind in CLASS_ORDER
        if any(company.filer_type == kind for company in companies)
    }


@router.get("/", response_class=HTMLResponse)
async def home(conn: Conn) -> HTMLResponse:
    return page("home.html", classes=await covered(conn))


@router.get("/search", response_class=HTMLResponse)
async def search(conn: Conn, q: Annotated[str, Query(max_length=200)] = "") -> Response:
    """Companies by ticker, name or CIK (service.companies.lookup, as MCP's lookup_company); a
    query that names one company's ticker or CIK opens its page."""
    text = q.strip()
    if not text:
        return page("search.html", query="", matches=(), message=None)
    try:
        found = (await lookup(conn, text)).data
    except InvalidParameter as exc:
        return page("search.html", status=400, query=text, matches=(), message=str(exc))
    if found.matches and (ticker := _named(text, found.matches[0])) is not None:
        return RedirectResponse(f"/companies/{ticker}", status_code=303)
    return page("search.html", query=text, matches=found.matches, message=None)


def _named(text: str, match: CompanyMatch) -> str | None:
    """The ticker to open when the query is one of the match's tickers or its CIK."""
    try:
        ticker = canonical_ticker(text)
    except TickerError:
        ticker = None
    if ticker is not None and ticker in (match.ticker, *match.other_tickers):
        return ticker
    if text.isdigit() and int(text) == match.cik:
        return match.ticker
    return None


@router.get("/companies/{ticker}", response_class=HTMLResponse)
async def company(
    request: Request,
    conn: Conn,
    ticker: str,
    days: Annotated[int, Query(description="The insider window in days.")] = INSIDER_WINDOWS[0],
    codes: Annotated[str, Query(pattern="^(ps|all)$")] = "ps",
) -> Response:
    security = await resolve_ticker(conn, ticker)
    if security.ticker != ticker:  # one address per company page: the SEC's spelling
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/companies/{security.ticker}{query}", status_code=308)
    profile = await company_profile(conn, security.cik)
    applies = {capability.kind: capability.applies for capability in profile.capabilities.kinds}
    snapshot = columns = table = chart = None
    if applies["fundamentals"]:
        snapshot = (await company_snapshot(conn, ticker)).data
        rows = (await get_financials(conn, ticker, period="Q", limit=HISTORY_QUARTERS)).data.rows
        columns, table = history(rows, CORE_METRICS)
        chart = chart_data(ticker, columns, table)
    # The filings with their check: when the company's watch last polled the SEC (D-135).
    filings = await recent_filings(conn, ticker, reports_only=True, limit=FILINGS_SHOWN)
    activity = None
    if applies["insiders"]:
        activity = (
            await insider_activity(
                conn, ticker, days=days, codes=_codes(codes), limit=INSIDER_TRADES
            )
        ).data
    return page(
        "company.html",
        security=security,
        profile=profile,
        snapshot=snapshot,
        columns=columns,
        table=table,
        chart=chart,
        filings=filings,
        activity=activity,
        codes=codes,
        windows=INSIDER_WINDOWS,
        holders=(await company_funds(conn, ticker)).data,
    )


@router.get("/companies/{ticker}/insiders", response_class=HTMLResponse)
async def insiders(
    request: Request,
    conn: Conn,
    ticker: str,
    days: int = INSIDER_WINDOWS[0],
    codes: Annotated[str, Query(pattern="^(ps|all)$")] = "ps",
    cursor: str | None = None,
) -> HTMLResponse:
    """The next page of a company's insider trades: rows for the company page to append (htmx),
    or a page of its own without scripts."""
    activity = (
        await insider_activity(
            conn, ticker, days=days, codes=_codes(codes), limit=INSIDER_TRADES, cursor=cursor
        )
    ).data
    if request.headers.get("HX-Request") == "true":
        return page("partials/insider_rows.html", activity=activity, codes=codes)
    return page("insiders.html", activity=activity, codes=codes, windows=INSIDER_WINDOWS)


def _codes(codes: str) -> list[str] | None:
    return ["all"] if codes == "all" else None  # None: purchases and sales (P, S)
