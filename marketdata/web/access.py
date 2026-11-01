"""Who may open the web UI and its proof pages (E15-S1, PLAN.md §9.14, OD-13).

- `UI_MODE=private` (the default) is for invited testers: every page needs HTTP Basic auth with
  an `api`-scope key as the password and any user name, as the proof pages did in M1 (D-104).
  Each request is logged in `api_requests` and counted against the key's rate limit, like a /v1
  request.
- `UI_MODE=public` opens the pages to anyone, at most UI_RATE_PER_MIN requests a minute from one
  IP address. The addresses are kept in memory for that minute, never stored, and the access log
  keeps them cut short (log.py); no request row is written, since there is no key to meter.

The static files (styles, scripts, their licenses) need no key in either mode: they hold no
data. Pages set no cookie: Basic auth needs none.
"""

import math
import time
from collections import deque
from collections.abc import Callable
from typing import Annotated

from fastapi import Request, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from marketdata.api.auth import identify
from marketdata.api.deps import Conn
from marketdata.service.clients import Caller
from marketdata.service.errors import RateLimited, Unauthorized

UI_REALM = "marketdata"
SIGN_IN = (
    "This site is open to invited testers: sign in with any user name and your API key as the "
    "password. Ask the operator for a key."
)
TOO_MANY = "Too many requests from your address in the last minute; try again shortly."
WINDOW_SECONDS = 60.0

STATIC_PREFIX = "/static/"
FAVICON = "/favicon.ico"
# Paths the API, the MCP server and the API reference answer; every other path is a page, whose
# errors are pages too.
_API_PATHS = ("/v1", "/mcp", "/health", "/docs", "/redoc", "/openapi.json")

ui_credentials = HTTPBasic(realm=UI_REALM, auto_error=False)


def is_page(path: str) -> bool:
    """Whether a path is the web UI's (a page, a proof page, a static file) rather than the
    API's, the MCP server's or the API reference's."""
    return not any(path == api or path.startswith(f"{api}/") for api in _API_PATHS)


def is_logged_page(path: str) -> bool:
    """Whether a request for this path is a page view the request log counts: pages and proof
    pages, not static files."""
    return is_page(path) and not path.startswith(STATIC_PREFIX) and path != FAVICON


class IpLimiter:
    """At most `per_minute` requests from one address in any minute (a sliding window), counted
    in memory. A refused request isn't counted, so an address that keeps asking gets in again a
    minute after its last counted request."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._swept_at = clock()

    def check(self, address: str) -> None:
        """Counts a request from `address`; raises RateLimited, with the seconds until one more
        is allowed, when the address made `per_minute` in the last minute."""
        now = self._clock()
        self._sweep(now)
        recent = self._seen.setdefault(address, deque())
        while recent and now - recent[0] >= WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= self.per_minute:
            raise RateLimited(
                TOO_MANY, retry_after=max(1, math.ceil(WINDOW_SECONDS - (now - recent[0])))
            )
        recent.append(now)

    def addresses(self) -> int:
        """How many addresses are remembered: those with a request in the last minute or so."""
        return len(self._seen)

    def _sweep(self, now: float) -> None:
        # Forget the addresses with no request in the last minute, once a minute.
        if now - self._swept_at < WINDOW_SECONDS:
            return
        self._seen = {
            address: recent
            for address, recent in self._seen.items()
            if recent and now - recent[-1] < WINDOW_SECONDS
        }
        self._swept_at = now


def client_address(request: Request) -> str:
    """The address the request came from, as the server sees it: behind a proxy, the proxy's,
    unless uvicorn is told to trust its forwarded headers."""
    return request.client.host if request.client else "unknown"


async def require_ui_access(
    request: Request,
    conn: Conn,
    credentials: Annotated[HTTPBasicCredentials | None, Security(ui_credentials)],
) -> Caller | None:
    """The caller of a page: its key in private mode; nobody in public mode, after the address's
    request is counted."""
    if request.app.state.ui_mode == "public":
        limiter: IpLimiter = request.app.state.ui_limiter
        limiter.check(client_address(request))
        return None
    if credentials is None or not credentials.password.strip():
        raise Unauthorized(SIGN_IN)
    return await identify(request, conn, credentials.password)
