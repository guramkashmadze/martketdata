"""E5a-S1: the §10.1 envelope and its JSON (D-096), keys (D-095) and the error envelope. Numbers
are written from the stored Decimal digit for digit, never through a float."""

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import httpx2
import pytest
from fastapi import FastAPI

from marketdata.api import errors
from marketdata.api.envelope import DISCLAIMER, dumps, respond
from marketdata.service.clients import KEY_PREFIX, hash_key, new_key
from marketdata.service.errors import (
    Forbidden,
    InvalidParameter,
    NotApplicable,
    NotFound,
    RateLimited,
    Unauthorized,
)
from marketdata.service.served import FlagRef, Served, Source


def test_decimals_are_json_numbers_with_every_digit() -> None:
    value = Decimal("123456789012345678.123456789")  # more digits than a float holds

    text = dumps({"value": value, "eps": Decimal("2.02"), "change_pct": Decimal("-0.5")})

    assert text == '{"value":123456789012345678.123456789,"eps":2.02,"change_pct":-0.5}'
    assert json.loads(text, parse_float=Decimal)["value"] == value


def test_times_are_utc_and_dates_iso() -> None:
    tbilisi = timezone(timedelta(hours=4))
    moment = datetime(2026, 7, 31, 0, 30, 28, tzinfo=tbilisi)

    assert dumps([moment, date(2026, 6, 27)]) == '["2026-07-30T20:30:28Z","2026-06-27"]'


def test_other_values() -> None:
    assert dumps({"a": None, "b": True, "c": 3, "d": ("x", "ü")}) == (
        '{"a":null,"b":true,"c":3,"d":["x","ü"]}'
    )


@pytest.mark.parametrize(
    "value",
    [1.5, Decimal("NaN"), Decimal("Infinity"), datetime(2026, 1, 1), object()],  # noqa: DTZ001
)
def test_values_that_are_never_written(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        dumps(value)


def test_the_envelope_carries_the_disclaimer() -> None:
    served = Served[dict[str, Decimal]](
        data={"value": Decimal("1.50")},
        sources=(Source(accession_no="0000320193-26-000018", form="8-K", filed_at=None, url="u"),),
        as_of=datetime(2026, 10, 29, 12, tzinfo=UTC),
        quality_flags=(FlagRef(code="stale_data", metric="revenue", period_end=None),),
    )

    body = json.loads(respond(served).body, parse_float=Decimal)

    assert list(body) == ["data", "sources", "as_of", "quality_flags", "disclaimer"]
    assert body["data"] == {"value": Decimal("1.50")}
    assert body["as_of"] == "2026-10-29T12:00:00Z"
    assert (
        body["disclaimer"]
        == DISCLAIMER
        == "Information from public filings. Not investment advice."
    )


def test_a_key_is_shown_once_and_stored_as_its_sha256() -> None:
    first, second = new_key(), new_key()

    assert first != second
    assert re.fullmatch(rf"{KEY_PREFIX}[A-Za-z0-9_-]{{43}}", first)
    assert hash_key(first) == hashlib.sha256(first.encode()).hexdigest()
    assert re.fullmatch("[0-9a-f]{64}", hash_key(first))


def _app() -> FastAPI:
    app = FastAPI()
    errors.install(app)
    raised = {
        "invalid": InvalidParameter("limit must be from 1 to 100"),
        "unauthorized": Unauthorized("Send your API key in the X-API-Key header."),
        "forbidden": Forbidden("wrong scope", 7),
        "missing": NotFound("No security has the ticker NOPE."),
        "fund": NotApplicable("A fund: fundamentals don't apply."),
        "limited": RateLimited("too many", 7, 12),
        "broken": RuntimeError("secret internals"),
    }

    @app.get("/raise/{name}")
    async def fail(name: str) -> None:  # noqa: ASYNC124 - an endpoint
        raise raised[name]

    @app.get("/limit")
    async def limit(limit: int = 20) -> int:  # noqa: ASYNC124 - an endpoint
        return limit

    return app


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("invalid", 400, "invalid_parameter"),
        ("unauthorized", 401, "unauthorized"),
        ("forbidden", 403, "forbidden"),
        ("missing", 404, "not_found"),
        ("fund", 422, "not_applicable"),
        ("limited", 429, "rate_limited"),
        ("broken", 500, "internal"),
    ],
)
async def test_errors_use_the_error_envelope(name: str, status: int, code: str) -> None:
    transport = httpx2.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/raise/{name}")

    assert response.status_code == status
    body = response.json()
    assert list(body) == ["error"] and body["error"]["code"] == code
    assert "secret" not in body["error"]["message"]
    if status == 429:
        assert response.headers["Retry-After"] == "12"


async def test_a_parameter_that_fails_validation_is_400_not_422() -> None:
    # 422 always means not_applicable (D-096).
    transport = httpx2.ASGITransport(app=_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.get("/limit", params={"limit": "many"})
        unknown = await client.get("/nowhere")
        post = await client.post("/limit")

    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_parameter"
    assert bad.json()["error"]["message"].startswith("limit: ")
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "not_found")
    assert (post.status_code, post.json()["error"]["code"]) == (405, "method_not_allowed")
