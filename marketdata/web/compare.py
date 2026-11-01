"""The compare page (E15-S3, PLAN.md §9.14): two to five companies side by side, each metric's
newest value of the period type for each (service.compare, as /v1/compare and MCP's
compare_companies), with its own period end and a link to its proof. Values are shown side by
side, never ranked, scored or coloured as better or worse (D-029).
"""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from marketdata.api.deps import Conn
from marketdata.service.compare import MAX_COMPANIES, MIN_COMPANIES, compare
from marketdata.service.errors import DomainError, InvalidParameter, NotApplicable, NotFound
from marketdata.service.financials import PERIODS
from marketdata.web.access import require_ui_access
from marketdata.web.render import page

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_ui_access)])

_SEPARATORS = re.compile(r"[\s,;]+")
_STATUS: tuple[tuple[type[DomainError], int], ...] = (
    (InvalidParameter, 400),
    (NotFound, 404),
    (NotApplicable, 422),
)


def tickers_of(text: str) -> list[str]:
    """The tickers in what was typed, in its order, each once: `aapl, msft msft` gives AAPL and
    MSFT; the service checks each."""
    return list(dict.fromkeys(part.upper() for part in _SEPARATORS.split(text) if part))


@router.get("/compare", response_class=HTMLResponse)
async def compare_page(
    conn: Conn,
    tickers: Annotated[str, Query(max_length=200)] = "",
    period: Annotated[str, Query(pattern="^(Q|FY|TTM)$")] = "TTM",
) -> HTMLResponse:
    """The form, and the comparison once it names two to five companies; a problem with what was
    typed is said next to the form."""
    wanted = tickers_of(tickers)
    context: dict[str, Any] = {"tickers": ", ".join(wanted), "period": period, "periods": PERIODS}
    if len(wanted) < MIN_COMPANIES:
        hint = None
        if wanted:
            hint = f"Add {MIN_COMPANIES - len(wanted)} to {MAX_COMPANIES - len(wanted)} tickers."
        return page("compare.html", comparison=None, message=hint, **context)
    try:
        comparison = (await compare(conn, wanted, period=period)).data
    except DomainError as exc:
        status = next((code for kind, code in _STATUS if isinstance(exc, kind)), 400)
        return page("compare.html", status=status, comparison=None, message=str(exc), **context)
    return page("compare.html", comparison=comparison, message=None, **context)
