"""E15-S1: the web UI's skeleton and access rule (PLAN.md §9.14). Private mode asks for an
`api`-scope key as the Basic auth password and logs each page against it; public mode is open,
with a limit per IP address and no request rows. Static files need no key. Pages set no cookie,
load nothing from another site and carry the disclaimer; their errors are pages."""

from typing import Any

import httpx2
import psycopg
import pytest
from web_pages import DISCLAIMER, basic, document, inline_scripts, loaded, public_ui

from marketdata.service.clients import create_client, create_key
from marketdata.web.assets import CHART_JS, HTMX, asset_url

Conn = psycopg.AsyncConnection[Any]


async def _rows(conn: Conn, path: str) -> list[tuple[Any, ...]]:
    found = await conn.execute(
        "select key_id, method, status from api_requests where path = %s order by id", [path]
    )
    return list(await found.fetchall())


async def test_a_private_page_needs_an_api_key(
    api: httpx2.AsyncClient, conn: Conn, api_key: str
) -> None:
    await create_client(conn, "reviewer")
    review = (await create_key(conn, "reviewer", scope="review")).key

    missing = await api.get("/")
    wrong = await api.get("/", headers=basic("mdk_not-a-key"))
    garbled = await api.get("/", headers={"Authorization": "Basic %%%"})
    scope = await api.get("/", headers=basic(review))
    allowed = await api.get("/", headers=basic(api_key, user="anyone at all"))

    for response in (missing, wrong, garbled):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == 'Basic realm="marketdata"'
        assert response.headers["content-type"].startswith("text/html")
        assert DISCLAIMER in response.text
    assert "sign in with any user name and your API key as the password" in missing.text
    assert scope.status_code == 403 and "text/html" in scope.headers["content-type"]
    assert allowed.status_code == 200, allowed.text
    assert "Verified data from SEC filings" in allowed.text and DISCLAIMER in allowed.text


async def test_private_pages_are_logged_and_counted_against_the_key(
    api: httpx2.AsyncClient, conn: Conn
) -> None:
    await create_client(conn, "tester")
    issued = await create_key(conn, "tester", rate_per_min=2)
    auth = basic(issued.key)

    first, second, third = [await api.get("/", headers=auth) for _ in range(3)]

    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 429]
    assert int(third.headers["retry-after"]) > 0
    assert "text/html" in third.headers["content-type"]
    assert await _rows(conn, "/") == [
        (issued.stored.key_id, "GET", 200),
        (issued.stored.key_id, "GET", 200),
        (issued.stored.key_id, "GET", 429),
    ]


async def test_a_public_page_needs_no_key_and_is_limited_per_address(
    conn: Conn, migrated_database: str
) -> None:
    async with public_ui(conn, migrated_database, per_minute=2) as ui:
        answers = [await ui.get("/") for _ in range(3)]
        api = await ui.get("/v1/coverage")
    async with public_ui(conn, migrated_database, per_minute=2, address="198.51.100.4") as other:
        elsewhere = await other.get("/")

    assert [answer.status_code for answer in answers] == [200, 200, 429]
    assert int(answers[2].headers["retry-after"]) > 0
    assert "Too many requests from your address" in answers[2].text
    assert elsewhere.status_code == 200
    assert "www-authenticate" not in answers[0].headers
    assert api.status_code == 401 and api.json()["error"]["code"] == "unauthorized"
    assert await _rows(conn, "/") == []  # no key to meter, no row


async def test_static_files_need_no_key_and_are_kept_a_year(
    api: httpx2.AsyncClient, conn: Conn
) -> None:
    style = await api.get(asset_url("app.css"))
    scripts = [await api.get(asset_url(name)) for name in (HTMX, CHART_JS)]
    icon = await api.get("/favicon.ico")

    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")
    for response in (style, *scripts, icon):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers["x-content-type-options"] == "nosniff"
    for script in scripts:
        assert "javascript" in script.headers["content-type"]
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert "?v=" in asset_url("app.css") and asset_url(HTMX).endswith("htmx-2.0.11/htmx.min.js")
    assert await _rows(conn, "/favicon.ico") == []
    found = await conn.execute("select count(*) from api_requests where path like '/static/%%'")
    assert await found.fetchone() == (0,)


async def test_a_page_sets_no_cookie_and_loads_nothing_from_elsewhere(
    api: httpx2.AsyncClient, api_key: str
) -> None:
    response = await api.get("/", headers=basic(api_key))
    page = document(response.text)

    assert "set-cookie" not in response.headers
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy
    assert response.headers["cache-control"] == "private, no-store"
    assert loaded(page) and all(url.startswith("/") for url in loaded(page))
    assert inline_scripts(page) == []
    assert page.xpath("//label[@for='q']") and page.xpath("/html/@lang") == ["en"]


@pytest.mark.parametrize(
    ("path", "status", "kind", "text"),
    [
        ("/nothing/here", 404, "text/html", "There is no page at /nothing/here"),
        ("/static/missing.css", 404, "text/html", "There is no page at /static/missing.css"),
        ("/v1/nothing", 404, "application/json", "There is no endpoint /v1/nothing"),
    ],
)
async def test_page_errors_are_pages_and_api_errors_stay_json(
    api: httpx2.AsyncClient, api_key: str, path: str, status: int, kind: str, text: str
) -> None:
    response = await api.get(path, headers={**basic(api_key), "X-API-Key": api_key})

    assert response.status_code == status
    assert response.headers["content-type"].startswith(kind)
    assert text in response.text


async def test_pages_are_left_out_of_the_api_reference(api: httpx2.AsyncClient) -> None:
    paths = (await api.get("/openapi.json")).json()["paths"]

    assert "/" not in paths and not [path for path in paths if path.startswith("/static")]
