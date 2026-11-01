"""E15: helpers for the web UI's tests: Basic auth headers, an app in public mode, and the parts
of a page the tests look at, read with lxml."""

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import psycopg
from conftest import connect_to
from lxml import html

from marketdata.api.app import create_app
from marketdata.config import Settings

DISCLAIMER = "Information from public filings. Not investment advice."


def basic(key: str, user: str = "tester") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@asynccontextmanager
async def public_ui(
    conn: psycopg.AsyncConnection[Any],
    database_url: str,
    *,
    per_minute: int = 60,
    address: str = "203.0.113.7",
) -> AsyncIterator[httpx2.AsyncClient]:
    """A client of the app with UI_MODE=public, its requests coming from `address`."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url=database_url, ui_mode="public", ui_rate_per_min=per_minute
    )
    app = create_app(settings, connect=connect_to(conn))
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False, client=(address, 4321))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def document(text: str) -> html.HtmlElement:
    return html.fromstring(text)


def loaded(page: html.HtmlElement) -> list[str]:
    """What a browser loads for the page: scripts, styles, icons and images."""
    return [
        *page.xpath("//script/@src"),
        *page.xpath("//link/@href"),
        *page.xpath("//img/@src"),
    ]


def inline_scripts(page: html.HtmlElement) -> list[html.HtmlElement]:
    """Scripts a browser would run from the page itself (data blocks don't run)."""
    return [
        script
        for script in page.xpath("//script[not(@src)]")
        if script.get("type", "text/javascript") not in ("application/json",)
    ]
