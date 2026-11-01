"""The web UI's pages (E15, PLAN.md §9.14): read-only, rendered on the server from the same service
functions as the API and the MCP server, so a page and the API never disagree. No SQL here.

Every page depends on the UI's access rule (web/access.py) through its router, so a page can't
be added without it. Pages are HTML, not part of the API contract: the API reference leaves them
out.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse

from marketdata.api.deps import Conn
from marketdata.service.status import StatusCache
from marketdata.web.access import FAVICON, require_ui_access
from marketdata.web.assets import CACHE_FOR_A_YEAR, STATIC_DIR
from marketdata.web.render import page

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_ui_access)])
# Browsers ask for /favicon.ico on their own; it holds no data, so it needs no key.
assets = APIRouter(include_in_schema=False)


# A route that awaits nothing stays async: FastAPI would run a plain function in a thread.
@router.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:  # noqa: ASYNC124
    return page("home.html")


@router.get("/status", response_class=HTMLResponse)
async def status(request: Request, conn: Conn) -> HTMLResponse:
    """Freshness, the track record and the accuracy report (E15-S4, PLAN.md §9.10): the report
    /v1/status serves, from the same minute's cache."""
    cache: StatusCache = request.app.state.status
    return page("status.html", report=(await cache.report(conn)).data)


@assets.get(FAVICON)
async def favicon() -> FileResponse:  # noqa: ASYNC124
    return FileResponse(
        STATIC_DIR / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": CACHE_FOR_A_YEAR, "X-Content-Type-Options": "nosniff"},
    )
