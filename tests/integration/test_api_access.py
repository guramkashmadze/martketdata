"""E5a-S1: `X-API-Key` on /v1 (401 missing or revoked, 403 wrong scope, 429 over the key's rate),
an `api_requests` row per /v1 request, and the §10.1 envelope on success. A probe route on a
router like the data routes' stands in for them here; S3's contract tests run the real ones."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import psycopg
import pytest
from fastapi import APIRouter, Depends, FastAPI

from marketdata.api.auth import require_api_key
from marketdata.api.envelope import DISCLAIMER, EnvelopeResponse, respond
from marketdata.service.clients import authenticate, create_key, revoke_key
from marketdata.service.errors import RateLimited
from marketdata.service.served import Served

Conn = psycopg.AsyncConnection[Any]


def _probe(app: FastAPI) -> None:
    router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @router.get("/probe")
    async def probe() -> EnvelopeResponse:  # noqa: ASYNC124 - an endpoint
        served = Served[dict[str, Decimal]](
            data={"value": Decimal("109417000000")}, sources=(), as_of=None, quality_flags=()
        )
        return respond(served)

    @router.get("/broken")
    async def broken() -> None:  # noqa: ASYNC124 - an endpoint
        raise RuntimeError("boom")

    app.include_router(router)


async def _logged(conn: Conn) -> list[tuple[Any, ...]]:
    cur = await conn.execute(
        "select key_id, method, path, status, duration_ms >= 0 from api_requests order by id"
    )
    return await cur.fetchall()


async def _key_id(conn: Conn, key: str) -> int:
    return (await authenticate(conn, key)).key_id


async def test_a_valid_key_gets_the_envelope(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    _probe(api_app)

    response = await api.get("/v1/probe", params={"x": "1"}, headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert response.json() == {
        "data": {"value": 109417000000},
        "sources": [],
        "as_of": None,
        "quality_flags": [],
        "disclaimer": DISCLAIMER,
    }
    assert await _logged(conn) == [(await _key_id(conn, api_key), "GET", "/v1/probe", 200, True)]


async def test_no_key_or_an_unknown_key_is_401(
    api_app: FastAPI, api: httpx2.AsyncClient, conn: Conn
) -> None:
    _probe(api_app)

    missing = await api.get("/v1/probe")
    unknown = await api.get("/v1/probe", headers={"X-API-Key": "mdk_not-a-key"})

    for response in (missing, unknown):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
    assert "X-API-Key" in missing.json()["error"]["message"]
    assert await _logged(conn) == [
        (None, "GET", "/v1/probe", 401, True),
        (None, "GET", "/v1/probe", 401, True),
    ]


async def test_a_revoked_key_is_401_and_logged_with_its_id(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    _probe(api_app)
    key_id = await _key_id(conn, api_key)
    await revoke_key(conn, key_id)

    response = await api.get("/v1/probe", headers={"X-API-Key": api_key})

    assert response.status_code == 401
    assert "revoked" in response.json()["error"]["message"]
    assert await _logged(conn) == [(key_id, "GET", "/v1/probe", 401, True)]


async def test_a_review_key_is_403(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    _probe(api_app)
    review = (await create_key(conn, "test client", scope="review")).key

    response = await api.get("/v1/probe", headers={"X-API-Key": review})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_over_the_rate_is_429_with_retry_after(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    _probe(api_app)
    limited = (await create_key(conn, "test client", rate_per_min=2)).key
    headers = {"X-API-Key": limited}

    statuses = [(await api.get("/v1/probe", headers=headers)).status_code for _ in range(4)]
    refused = await api.get("/v1/probe", headers=headers)

    assert statuses == [200, 200, 429, 429]
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    # Another key of the same client has its own limit.
    assert (await api.get("/v1/probe", headers={"X-API-Key": api_key})).status_code == 200


async def test_the_window_is_the_last_minute_and_refused_requests_dont_count(
    conn: Conn, api_key: str
) -> None:
    limited = await create_key(conn, "test client", rate_per_min=2)
    now = datetime(2026, 10, 29, 12, tzinfo=UTC)
    rows = [
        (now - timedelta(seconds=61), 200),  # outside the window
        (now - timedelta(seconds=50), 200),
        (now - timedelta(seconds=40), 429),  # refused: not counted
    ]
    for at, status in rows:
        await conn.execute(
            "insert into api_requests (key_id, at, method, path, status, duration_ms) "
            "values (%s, %s, 'GET', '/v1/probe', %s, 1)",
            [limited.stored.key_id, at, status],
        )

    assert (await authenticate(conn, limited.key, now=now)).key_id == limited.stored.key_id
    await conn.execute(
        "insert into api_requests (key_id, at, method, path, status, duration_ms) "
        "values (%s, %s, 'GET', '/v1/probe', 200, 1)",
        [limited.stored.key_id, now - timedelta(seconds=30)],
    )
    with pytest.raises(RateLimited) as refused:
        await authenticate(conn, limited.key, now=now)
    assert refused.value.retry_after == 10  # the request 50 seconds ago leaves the window in 10


async def test_a_server_error_is_500_and_logged(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    _probe(api_app)

    response = await api.get("/v1/broken", headers={"X-API-Key": api_key})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal"
    assert "boom" not in response.text
    assert await _logged(conn) == [(await _key_id(conn, api_key), "GET", "/v1/broken", 500, True)]


async def test_unknown_v1_paths_are_logged_and_health_is_not(
    api: httpx2.AsyncClient, conn: Conn
) -> None:
    response = await api.get("/v1/nowhere")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert await _logged(conn) == [(None, "GET", "/v1/nowhere", 404, True)]
