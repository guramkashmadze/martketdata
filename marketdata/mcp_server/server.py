"""The MCP server at /mcp (E6, PLAN.md §10.3): the official SDK's server over Streamable HTTP,
inside the API's FastAPI app, with the tools of mcp_server/tools.py (D-107).

Every request needs an `api`-scope key in X-API-Key, checked as on /v1 (service/clients.py).
A refused request gets the §10.1 error envelope (401, 403, or 429 with Retry-After) and an
`api_requests` row with the path /mcp. Each tool call writes one row: method MCP, the tool's
name as the path, and the status of its outcome (200; 400 for arguments that fail validation;
404, 422 or 500 as the API answers them), so tool calls count against the key's rate like API
requests. Protocol messages (initialize, tools/list, ping) are checked for the key but not
logged. A failed tool call answers the error envelope as the tool's error text.

The server keeps no sessions and answers JSON, so any API process can answer any request. It
accepts the Host and Origin of this machine and of PUBLIC_BASE_URL only (DNS rebinding).
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import psycopg
import structlog
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, InputRequiredResult
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from marketdata.config import Settings
from marketdata.mcp_server.tools import Connect, Tools
from marketdata.service.clients import Caller, authenticate, record_request
from marketdata.service.errors import INTERNAL_MESSAGE, AccessDenied, DomainError, answer_of
from marketdata.service.json_text import dumps

log = structlog.get_logger(__name__)

PATH = "/mcp"
METHOD = "MCP"  # api_requests.method of MCP requests (§10.3)
CALLER = "mcp_caller"  # where the key gate leaves the Caller in the request's state

INSTRUCTIONS = """\
Verified data from SEC filings of US public companies: earnings releases, financial metrics \
from 10-Q and 10-K reports, and filings with plain-language event labels. Every value carries \
its proof: the filing it came from (its accession number; `sources` gives each filing's SEC \
link), how it was found and the checks it passed. Cite the SEC links when you use a value. \
A missing or unverified value is null with the reason: say so, and never estimate one. \
Amounts are in base units (USD, shares, USD per share); margins and growth are percents. Use \
lookup_company when you don't know a company's ticker. The data is information from public \
filings, not investment advice."""


def error_text(code: str, message: str) -> str:
    """The §10.1 error envelope, as a failed tool call's text."""
    return dumps({"error": {"code": code, "message": message}})


class McpServer(MCPServer[Any]):
    """The SDK's server, with each tool call logged (`api_requests`) and its failure answered
    with the error envelope."""

    def __init__(self, connect: Callable[[], Connect], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._connect = connect

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        started = datetime.now(UTC)
        clock = time.perf_counter()
        failure: ToolError | None = None
        try:
            result = await super().call_tool(name, arguments, context)
        except ToolError as exc:  # UnexpectedToolError is one
            failure = exc
        status, answer = (200, None) if failure is None else await self._answer(name, failure)
        await self._record(context, name, started, clock, status)
        if answer is not None:
            raise answer from failure
        return result

    async def _answer(self, tool: str, failure: ToolError) -> tuple[int, ToolError]:
        """The status and error text of a failed call. The SDK raises ToolError for an unknown
        tool and for arguments that fail the input schema, and UnexpectedToolError around
        anything a tool raises: here a domain error, else a bug, logged with its traceback."""
        cause = failure.__cause__
        if isinstance(failure, UnexpectedToolError):
            if isinstance(cause, DomainError):
                status, code = answer_of(cause)
                return status, ToolError(error_text(code, str(cause)))
            log.error("mcp_tool_failed", tool=tool, exc_info=cause)
            return 500, ToolError(error_text("internal", INTERNAL_MESSAGE))
        if isinstance(cause, ValidationError):
            return 400, ToolError(error_text("invalid_parameter", _problems(cause)))
        if tool not in {each.name for each in await self.list_tools()}:
            message = f"There is no tool {tool!r}; tools/list lists the tools."
            return 404, ToolError(error_text("not_found", message))
        return 400, ToolError(error_text("invalid_parameter", str(failure)))

    async def _record(
        self,
        context: Context[Any, Any] | None,
        tool: str,
        started: datetime,
        clock: float,
        status: int,
    ) -> None:
        caller = caller_of(context)
        duration_ms = round((time.perf_counter() - clock) * 1000)
        try:
            async with self._connect()() as conn:
                await record_request(
                    conn,
                    key_id=caller.key_id if caller is not None else None,
                    at=started,
                    method=METHOD,
                    path=tool,
                    status=status,
                    duration_ms=duration_ms,
                )
        except (psycopg.Error, OSError) as exc:
            log.warning("mcp_request_not_logged", tool=tool, status=status, error=str(exc))


def caller_of(context: Context[Any, Any] | None) -> Caller | None:
    """The key the key gate let the call's request in with; None outside an HTTP request."""
    if context is None:
        return None
    try:
        request = context.request_context.request
    except ValueError:  # no request: a call from inside the process
        return None
    caller = getattr(getattr(request, "state", None), CALLER, None)
    return caller if isinstance(caller, Caller) else None


def _problems(error: ValidationError) -> str:
    """'limit: Input should be less than or equal to 12', as the API words a bad parameter."""
    problems = []
    for each in error.errors():
        where = ".".join(str(part) for part in each.get("loc", ())) or "arguments"
        problems.append(f"{where}: {each.get('msg', 'invalid')}")
    return "; ".join(problems) or "Invalid arguments."


class KeyGate:
    """Checks the key of every request to /mcp before the SDK sees it, and leaves the caller
    in the request's state for the tool call's log row."""

    def __init__(self, app: ASGIApp, connect: Callable[[], Connect]) -> None:
        self.app = app
        self._connect = connect

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = datetime.now(UTC)
        clock = time.perf_counter()
        try:
            async with self._connect()() as conn:
                caller = await authenticate(conn, Headers(scope=scope).get("x-api-key"))
        except AccessDenied as exc:
            await self._refuse(scope, receive, send, exc, started, clock)
            return
        scope.setdefault("state", {})[CALLER] = caller
        await self.app(scope, receive, send)

    async def _refuse(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        refused: AccessDenied,
        started: datetime,
        clock: float,
    ) -> None:
        status, code = answer_of(refused)
        headers = None
        if refused.retry_after is not None:
            headers = {"Retry-After": str(refused.retry_after)}
        response = JSONResponse(
            {"error": {"code": code, "message": str(refused)}}, status_code=status, headers=headers
        )
        await response(scope, receive, send)
        duration_ms = round((time.perf_counter() - clock) * 1000)
        try:
            async with self._connect()() as conn:
                await record_request(
                    conn,
                    key_id=refused.key_id,
                    at=started,
                    method=METHOD,
                    path=PATH,
                    status=status,
                    duration_ms=duration_ms,
                )
        except (psycopg.Error, OSError) as exc:
            log.warning("mcp_request_not_logged", path=PATH, status=status, error=str(exc))


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """The Host and Origin headers the server accepts: this machine's, and PUBLIC_BASE_URL's
    (the live VM answers behind Caddy, which passes the public host on)."""
    hosts = ["localhost", "127.0.0.1", "[::1]"]
    origins = [f"http://{host}" for host in hosts]
    if settings.base_url:
        public = urlsplit(settings.base_url)
        hosts.append(public.netloc)
        origins.append(f"{public.scheme}://{public.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*hosts, *(f"{host}:*" for host in hosts)],
        allowed_origins=[*origins, *(f"{origin}:*" for origin in origins)],
    )


@dataclass(frozen=True)
class McpEndpoint:
    server: McpServer
    app: ASGIApp  # the /mcp route: the key gate in front of the SDK's transport

    def run(self) -> AbstractAsyncContextManager[None]:
        """Runs the SDK's session manager: in the API's lifespan, or around a test."""
        return self.server.session_manager.run()


def create_mcp(settings: Settings, connect: Callable[[], Connect]) -> McpEndpoint:
    """The MCP endpoint of an API app; `connect` gives the app's connections when called."""
    with _logging_kept():
        server = McpServer(
            connect,
            name="marketdata",
            title="marketdata: verified data from SEC filings",
            instructions=INSTRUCTIONS,
            version="1",
        )
    Tools(connect).register(server)
    # Builds the server's session manager; the route below serves it with the key gate in
    # front, at exactly /mcp (a mount would redirect POST /mcp to /mcp/).
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(settings),
    )
    return McpEndpoint(server, KeyGate(StreamableHTTPASGIApp(server.session_manager), connect))


@contextmanager
def _logging_kept() -> Iterator[None]:
    """The SDK's server sets up logging when it is built (logging.basicConfig with its own
    handler); log.py sets up this process's logging, so the root logger is put back."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
