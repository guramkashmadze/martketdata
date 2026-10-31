"""E6: MCP clients of the API app in the test's event loop, over Streamable HTTP through httpx2's
ASGI transport, as Claude or the MCP Inspector connect to /mcp (no network).

The app's MCP session manager runs inside `serving`, as the API's lifespan runs it: once per
app, and entered and left in the same task (anyio), so tests use these context managers rather
than fixtures. `connected` is `serving` with one client; a test with several opens them with
`session` inside one `serving`.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import httpx2
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, TextContent

BASE = "http://localhost"  # a Host the server accepts (DNS rebinding protection)
URL = f"{BASE}/mcp"
# What a Streamable HTTP client sends with every POST.
POST_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def initialize(request_id: int = 1) -> dict[str, Any]:
    """The JSON-RPC message a 2025 client opens with."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


def tool_call(name: str, arguments: dict[str, Any], request_id: int = 2) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def http(app: FastAPI, key: str | None = None) -> httpx2.AsyncClient:
    headers = {"X-API-Key": key} if key is not None else {}
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx2.AsyncClient(transport=transport, base_url=BASE, headers=headers)


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[None]:
    """The app's MCP session manager, running as in the API's lifespan."""
    async with app.state.mcp.run():
        yield


@asynccontextmanager
async def session(app: FastAPI, key: str, *, mode: str = "legacy") -> AsyncIterator[Client]:
    """An MCP client of the app with this key, inside `serving`: `legacy` opens with initialize,
    as the 2025 protocol's clients do; a protocol version such as 2026-07-28 sends single
    requests."""
    async with (
        http(app, key) as client,
        Client(streamable_http_client(URL, http_client=client), mode=mode) as opened,
    ):
        yield opened


@asynccontextmanager
async def connected(app: FastAPI, key: str, *, mode: str = "legacy") -> AsyncIterator[Client]:
    async with serving(app), session(app, key, mode=mode) as client:
        yield client


def text_of(result: CallToolResult) -> str:
    (content,) = result.content
    assert isinstance(content, TextContent)
    return content.text


def parsed(result: CallToolResult) -> Any:
    """A tool's answer, numbers as Decimal so every digit is compared."""
    return json.loads(text_of(result), parse_float=Decimal)
