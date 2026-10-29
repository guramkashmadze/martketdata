"""E5a-S4: earnings, the snapshot, financials and comparisons answer `not_applicable` (422) with
the §9.1 reason for funds, foreign private issuers and companies outside the universe; filings
and the coverage list answer for every class (D-099)."""

from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import seed

from marketdata.service.compare import compare
from marketdata.service.earnings import earnings_feed, latest_earnings
from marketdata.service.errors import NotApplicable
from marketdata.service.financials import get_financials
from marketdata.service.snapshot import company_snapshot

Conn = psycopg.AsyncConnection[Any]
FUND = "FNDX (Index Fund Trust): A fund: "
FPI = "FPIX (Foreign Issuer plc): A foreign private issuer: "


@pytest.fixture
async def headers(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return {"X-API-Key": api_key}


@pytest.mark.parametrize(
    ("path", "params", "message"),
    [
        ("/v1/companies/FNDX", {}, FUND + "fundamentals don't apply."),
        ("/v1/companies/fndx/earnings/latest", {}, FUND + "earnings releases don't apply."),
        ("/v1/companies/FNDX/financials", {}, FUND + "fundamentals don't apply."),
        ("/v1/earnings", {"ticker": "FNDX"}, FUND + "earnings releases don't apply."),
        ("/v1/compare", {"tickers": "ACME,FNDX"}, FUND + "fundamentals don't apply."),
        (
            "/v1/companies/FPIX",
            {},
            FPI + "its 20-F and 40-F annual reports are not covered yet.",
        ),
        (
            "/v1/companies/FPIX/earnings/latest",
            {},
            FPI + "its results come in 6-K reports, which are not covered yet.",
        ),
        (
            "/v1/companies/FPIX/financials",
            {"period": "TTM"},
            FPI + "its 20-F and 40-F annual reports are not covered yet.",
        ),
        ("/v1/companies/OUTC/financials", {}, "OUTC (Outside Corp): Not in the covered universe."),
    ],
)
async def test_data_that_doesnt_apply_is_422_with_the_reason(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    path: str,
    params: dict[str, str],
    message: str,
) -> None:
    response = await api.get(path, params=params, headers=headers)

    assert response.status_code == 422
    assert response.json() == {"error": {"code": "not_applicable", "message": message}}


async def test_filings_and_coverage_answer_for_every_class(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    filings = await api.get("/v1/companies/FNDX/filings", headers=headers)
    covered = await api.get("/v1/coverage", headers=headers)

    assert filings.status_code == 200 and filings.json()["data"]["filings"] == []
    classes = {entry["ticker"]: entry["filer_type"] for entry in covered.json()["data"]["tickers"]}
    assert (classes["FNDX"], classes["FPIX"]) == ("fund", "foreign_private_issuer")


async def test_the_service_functions_raise_not_applicable(conn: Conn) -> None:
    await seed(conn)

    with pytest.raises(NotApplicable, match="A fund: earnings releases don't apply"):
        await latest_earnings(conn, "FNDX")
    with pytest.raises(NotApplicable, match="its results come in 6-K reports"):
        await earnings_feed(conn, ticker="FPIX")
    with pytest.raises(NotApplicable, match="20-F and 40-F"):
        await get_financials(conn, "FPIX")
    with pytest.raises(NotApplicable, match="A fund: fundamentals don't apply"):
        await company_snapshot(conn, "FNDX")
    with pytest.raises(NotApplicable, match="FNDX"):
        await compare(conn, ["FNDX", "ACME"])
