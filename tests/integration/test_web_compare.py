"""E15-S3: the compare page (PLAN.md §9.14): two to five companies side by side from
service.compare, each value with its own period end and a link to its proof, never ranked."""

from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import seed
from web_pages import DISCLAIMER, basic, document

Conn = psycopg.AsyncConnection[Any]


@pytest.fixture
async def auth(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return basic(api_key)


async def test_two_companies_side_by_side_each_value_with_its_proof(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    response = await api.get("/compare", params={"tickers": "acme, beta beta"}, headers=auth)

    assert response.status_code == 200, response.text
    page = response.text
    acme = '<a class="v" href="/proof/metrics/ACME/revenue/TTM/2026-06-30">0.0038</a>'
    beta = '<a class="v" href="/proof/metrics/BETA/revenue/TTM/2026-06-30">0.005</a>'
    assert acme in page and beta in page
    assert page.index(">ACME</a>") < page.index(">BETA</a>")  # in the order typed
    assert 'title="outlier_jump"' in page  # BETA's flag, never hiding the value
    assert 'value="ACME, BETA"' in page and "never ranked" in page and DISCLAIMER in page
    rows = document(page).xpath("//tbody/tr/th/text()")
    assert rows[:2] == ["Revenue", "Operating income"]  # the core set, in its order


async def test_the_period_type_can_change(api: httpx2.AsyncClient, auth: dict[str, str]) -> None:
    page = (await api.get("/compare?tickers=ACME,BETA&period=Q", headers=auth)).text

    assert '<a class="v" href="/proof/metrics/ACME/revenue/Q/2026-06-30">0.001</a>' in page
    assert '<option value="Q" selected>Quarter</option>' in page


@pytest.mark.parametrize(
    ("tickers", "status", "text"),
    [
        ("", 200, "Two to five companies side by side"),
        ("ACME", 200, "Add 1 to 4 tickers."),
        ("ACME,ACME-PA", 400, "are the same company"),
        ("ACME,FNDX", 422, "A fund"),
        ("ACME,NOPE", 404, "NOPE"),
        ("A,B,C,D,E,F", 400, "compare takes 2 to 5 tickers"),
    ],
)
async def test_what_is_typed_is_answered_next_to_the_form(
    api: httpx2.AsyncClient, auth: dict[str, str], tickers: str, status: int, text: str
) -> None:
    response = await api.get("/compare", params={"tickers": tickers}, headers=auth)

    assert response.status_code == status
    assert text in response.text and '<form class="controls" action="/compare"' in response.text
    assert "<table" not in response.text
