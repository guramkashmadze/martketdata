"""The fund pages (E15-S6, PLAN.md §9.8, D-120): the curated managers by name, and a page per
manager with a quarter's positions, largest value first and then those it no longer reports,
each with its weight, its change from the quarter before and a link to its proof. Every page
shows the report period and the filing date: a 13F is filed up to 45 days after the quarter.
Nothing ranks managers (D-029).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from marketdata.api.deps import Conn
from marketdata.service.funds import CHANGE_TYPES, fund_holdings, list_funds, reported_quarters
from marketdata.web.access import require_ui_access
from marketdata.web.render import page

router = APIRouter(
    prefix="/funds", include_in_schema=False, dependencies=[Depends(require_ui_access)]
)

POSITIONS = 100  # a page of positions; more load on request
CHANGE_PATTERN = "^(" + "|".join(CHANGE_TYPES) + ")$"


@router.get("", response_class=HTMLResponse)
async def funds(conn: Conn) -> HTMLResponse:
    return page("funds.html", funds=(await list_funds(conn)).data)


@router.get("/{manager_cik}", response_class=HTMLResponse)
async def fund(
    request: Request,
    conn: Conn,
    manager_cik: int,
    quarter: str | None = None,
    change: Annotated[str | None, Query(pattern=CHANGE_PATTERN)] = None,
    cursor: str | None = None,
) -> HTMLResponse:
    """A quarter's positions (the latest by default), a page at a time; `change` keeps one
    change type. With htmx, only the next page's rows."""
    held = (
        await fund_holdings(
            conn,
            manager_cik,
            quarter=quarter,
            change=[change] if change else None,
            limit=POSITIONS,
            cursor=cursor,
        )
    ).data
    if request.headers.get("HX-Request") == "true":
        return page("partials/position_rows.html", held=held, change=change)
    return page(
        "fund.html",
        held=held,
        change=change,
        quarters=await reported_quarters(conn, manager_cik),
        first_page=cursor is None,
    )
