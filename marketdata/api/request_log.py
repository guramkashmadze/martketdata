"""The request log (E5a-S1): one `api_requests` row per /v1 request and proof page (E5b),
refused ones included.

The row holds the key (none when no valid key was sent), when the request started, its method,
its path without the query string, the status it was answered with and how long it took. It is
written after the response, on a connection of its own; a failed write is logged and the client
never sees it. Requests carry tickers and parameters only, no end-user data (guardrail 2).
"""

import time
from datetime import UTC, datetime
from typing import Any

import psycopg
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from marketdata.api.deps import Connect
from marketdata.service.clients import record_request

log = structlog.get_logger(__name__)


def is_logged(path: str) -> bool:
    return path == "/v1" or path.startswith(("/v1/", "/proof/"))


class RequestLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_logged(scope["path"]):
            await self.app(scope, receive, send)
            return
        state: dict[str, Any] = scope.setdefault("state", {})  # request.state (api/auth.py)
        started = datetime.now(UTC)
        clock = time.perf_counter()
        status: int | None = None

        async def send_and_note(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        error: Exception | None = None
        try:
            await self.app(scope, receive, send_and_note)
        except Exception as exc:
            # The server's error handler (api/errors.py) answers it 500, unless a response had
            # started already.
            error = exc
        await self._record(scope, state, started, clock, status or 500)
        if error is not None:
            raise error

    async def _record(
        self, scope: Scope, state: dict[str, Any], started: datetime, clock: float, status: int
    ) -> None:
        connect: Connect = scope["app"].state.connect
        duration_ms = round((time.perf_counter() - clock) * 1000)
        try:
            async with connect() as conn:
                await record_request(
                    conn,
                    key_id=state.get("key_id"),
                    at=started,
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    duration_ms=duration_ms,
                )
        except (psycopg.Error, OSError) as exc:
            log.warning("api_request_not_logged", path=scope["path"], status=status, error=str(exc))
