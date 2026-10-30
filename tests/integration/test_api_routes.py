"""E5a-S3: the /v1 routes of PLAN.md §10.2. A contract test per route checks the envelope and
that every value carries a complete proof object (§9.10); the routes need a key, take tickers
as people write them, page through lists and are documented at /docs with examples."""

from decimal import Decimal
from typing import Any

import httpx2
import psycopg
import pytest
from api_contract import check_envelope
from api_seed import ACME, Q2_10Q, Q2_RELEASE, Q3_RELEASE, seed
from fastapi import FastAPI

from marketdata.api.envelope import DISCLAIMER

Conn = psycopg.AsyncConnection[Any]

# Every /v1 data route (its path as declared), a request to it and the parameters it takes.
ROUTES: list[tuple[str, str, dict[str, str]]] = [
    ("/v1/coverage", "/v1/coverage", {}),
    ("/v1/companies/{ticker}", "/v1/companies/acme", {}),
    ("/v1/companies/{ticker}/earnings/latest", "/v1/companies/ACME/earnings/latest", {}),
    ("/v1/companies/{ticker}/financials", "/v1/companies/ACME/financials", {"period": "TTM"}),
    ("/v1/companies/{ticker}/filings", "/v1/companies/ACME/filings", {"limit": "5"}),
    ("/v1/earnings", "/v1/earnings", {}),
    ("/v1/earnings/{accession_no}", f"/v1/earnings/{Q2_RELEASE}", {}),
    ("/v1/compare", "/v1/compare", {"tickers": "acme,BETA", "metrics": "revenue,net_income"}),
    ("/v1/changes", "/v1/changes", {}),
]


@pytest.fixture
async def headers(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return {"X-API-Key": api_key}


def test_every_v1_route_has_a_contract_test(api_app: FastAPI) -> None:
    documented = {path for path in api_app.openapi()["paths"] if path.startswith("/v1")}

    assert documented == {path for path, _, _ in ROUTES}


@pytest.mark.parametrize(("declared", "path", "params"), ROUTES, ids=[r[0] for r in ROUTES])
async def test_a_route_needs_a_key(
    api: httpx2.AsyncClient, declared: str, path: str, params: dict[str, str]
) -> None:
    response = await api.get(path, params=params)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(("declared", "path", "params"), ROUTES, ids=[r[0] for r in ROUTES])
async def test_route_contract(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    declared: str,
    path: str,
    params: dict[str, str],
) -> None:
    response = await api.get(path, params=params, headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json(parse_float=Decimal)
    check_envelope(body)


async def test_numbers_are_json_numbers(api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
    response = await api.get(f"/v1/earnings/{Q2_RELEASE}", headers=headers)

    assert '"value":1000,' in response.text
    assert '"value":1.25,' in response.text
    assert '"change_pct":5.3,' in response.text
    revenue = response.json()["data"]["facts"][0]
    assert revenue["proof"]["cross_check"] == {
        "status": "matched",
        "filing_accession_no": Q2_10Q,
        "filing_value": 1000,
    }


async def test_tickers_as_people_write_them(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    by_class = await api.get("/v1/companies/acme.pa/filings", headers=headers)
    unknown = await api.get("/v1/companies/NOPE/earnings/latest", headers=headers)
    malformed = await api.get("/v1/companies/no*such/financials", headers=headers)

    assert (by_class.status_code, by_class.json()["data"]["ticker"]) == (200, "ACME-PA")
    assert by_class.json()["data"]["cik"] == ACME
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "not_found")
    assert (malformed.status_code, malformed.json()["error"]["code"]) == (400, "invalid_parameter")


async def test_lists_come_a_page_at_a_time(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    tickers: list[str] = []
    params = {"limit": "2"}
    while True:
        page = (await api.get("/v1/coverage", params=params, headers=headers)).json()["data"]
        tickers += [entry["ticker"] for entry in page["tickers"]]
        if page["next_cursor"] is None:
            break
        params = {"limit": "2", "cursor": page["next_cursor"]}
    too_many = await api.get("/v1/coverage", params={"limit": "101"}, headers=headers)
    bad_cursor = await api.get("/v1/earnings", params={"cursor": "x"}, headers=headers)
    bad_period = await api.get(
        "/v1/companies/ACME/financials", params={"period": "H1"}, headers=headers
    )

    assert tickers == ["ACME", "ACME-PA", "BETA", "FNDX", "FPIX"]
    for response in (too_many, bad_cursor, bad_period):
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "invalid_parameter",
        )
    assert too_many.json()["error"]["message"].startswith("limit: ")


async def test_the_feed_filters(api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
    since = await api.get("/v1/earnings", params={"since": "2026-05-01"}, headers=headers)
    by_ticker = await api.get("/v1/earnings", params={"ticker": "acme"}, headers=headers)
    bad = await api.get("/v1/earnings", params={"since": "yesterday"}, headers=headers)

    assert [r["release"]["accession_no"] for r in since.json()["data"]["releases"]] == [Q2_RELEASE]
    assert len(by_ticker.json()["data"]["releases"]) == 2
    assert bad.status_code == 400 and "since must be a day" in bad.json()["error"]["message"]


async def test_the_latest_release_may_have_failed(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    data = (await api.get("/v1/companies/ACME/earnings/latest", headers=headers)).json()["data"]

    assert (data["release"]["accession_no"], data["release"]["status"]) == (Q3_RELEASE, "failed")
    assert data["facts"][0]["value"] is None and data["facts"][0]["reasons"]


async def test_the_reference_documents_every_route(api: httpx2.AsyncClient) -> None:
    document = (await api.get("/openapi.json")).json()
    docs = await api.get("/docs")

    assert docs.status_code == 200 and "swagger" in docs.text.lower()
    schemes = document["components"]["securitySchemes"]
    assert [scheme["name"] for scheme in schemes.values()] == ["X-API-Key"]
    for declared, _, _ in ROUTES:
        operation = document["paths"][declared]["get"]
        assert operation["security"], declared
        example = operation["responses"]["200"]["content"]["application/json"]["example"]
        assert example["disclaimer"] == DISCLAIMER, declared
        assert {"400", "401", "403", "429"} <= set(operation["responses"]), declared
    # Values are JSON numbers (D-096), not the strings pydantic would describe.
    schemas = document["components"]["schemas"]
    fact = schemas["EarningsFact"]["properties"]["value"]
    assert {"$ref": "#/components/schemas/Number"} in fact["anyOf"]
    assert schemas["Number"]["type"] == "number"
