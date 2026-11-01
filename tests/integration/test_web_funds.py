"""E15-S6: the fund pages (PLAN.md §9.8, D-120): the curated managers by name, and a page per
manager with a quarter's positions, weights and changes, each showing the report period and the
filing date and linking to its proof; a position's proof page adds up its 13F lines."""

from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import ACME_CUSIP, BETA_CUSIP, MANAGER, MANAGER_13F, seed
from web_pages import DISCLAIMER, basic, document

Conn = psycopg.AsyncConnection[Any]
SEC = "https://www.sec.gov/Archives/edgar/data/"


@pytest.fixture
async def auth(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return basic(api_key)


async def _page(api: httpx2.AsyncClient, auth: dict[str, str], path: str) -> str:
    response = await api.get(path, headers=auth)
    assert response.status_code == 200, response.text
    return response.text


async def test_the_fund_list_names_each_manager_with_its_latest_report(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, "/funds")

    assert f'<a href="/funds/{MANAGER}">Test Capital</a>' in page and DISCLAIMER in page
    assert "TEST CAPITAL LLC" in page and "2026-06-30" in page and "2026-08-14" in page
    assert f'<a class="t" href="/funds/{MANAGER}">4,100,000</a>' in page
    assert 'aria-current="page"' in page


async def test_a_fund_page_shows_a_quarters_positions_with_their_proof(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, f"/funds/{MANAGER}")

    acme = f"/proof/funds/{MANAGER}/2026Q2/{ACME_CUSIP}"
    beta = f"/proof/funds/{MANAGER}/2026Q2/{BETA_CUSIP}?put_call=CALL"
    assert "<strong>2026Q2</strong>" in page and "2026-08-14" in page
    for number in ("40,000", "4,000,000", "100%"):
        assert f'<a class="v" href="{acme}">{number}</a>' in page
    assert f'<a class="v" href="{beta.replace("&", "&amp;")}">1,000</a>' in page
    assert '<span class="badge">CALL</span>' in page
    assert 'href="/companies/ACME">ISSUER</a>' in page  # the issuer as filed, linked by FIGI
    assert "no report for the quarter before" in page and "no_prior_report" in page
    # The report read: its unit and the summary page's line count and total.
    assert "whole dollars (SEC FAQ 62)" in page and "summary page 4,100,000" in page
    assert f'href="{SEC}9009/000900900026000003/t.xml"' in page


@pytest.mark.parametrize(
    ("path", "status", "text"),
    [
        ("/funds/1234", 404, "CIK 1234 is not a curated manager"),
        (f"/funds/{MANAGER}?quarter=2020Q1", 404, "has no 13F report for 2020Q1"),
        (f"/funds/{MANAGER}?quarter=soon", 400, "soon"),
        (f"/funds/{MANAGER}?change=sold", 400, "change"),
    ],
)
async def test_fund_page_errors_are_pages(
    api: httpx2.AsyncClient, auth: dict[str, str], path: str, status: int, text: str
) -> None:
    response = await api.get(path, headers=auth)

    assert response.status_code == status
    assert text in response.text and DISCLAIMER in response.text


async def test_a_change_type_keeps_its_positions(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, f"/funds/{MANAGER}?change=new")

    assert "No position of this kind in the report." in page
    assert "<strong>New (0)</strong>" in page


async def test_more_positions_load_a_page_at_a_time(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    for number in range(101):  # 103 positions: a page of 100, then 3
        await conn.execute(
            "insert into fund_positions (manager_cik, report_period, cusip, share_type, "
            "name_of_issuer, shares, value_usd, lines, source_accessions) values "
            "(%s, '2026-06-30', %s, 'SH', 'OTHER', 1, 1, 1, %s)",
            [MANAGER, f"9{number:08d}", [MANAGER_13F]],
        )

    page = document(await _page(api, auth, f"/funds/{MANAGER}"))
    (more,) = page.xpath("//tr[@id='positions-more']//a")
    rows = await api.get(more.get("hx-get"), headers={**auth, "HX-Request": "true"})

    assert len(page.xpath("//h2[@id='positions']/following::table[1]/tbody/tr")) == 101
    assert rows.status_code == 200 and "<html" not in rows.text
    assert len(document(f"<table>{rows.text}</table>").xpath("//tr")) == 3


async def test_a_positions_proof_adds_up_its_lines(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, f"/proof/funds/{MANAGER}/2026Q2/{ACME_CUSIP}")
    option = await _page(api, auth, f"/proof/funds/{MANAGER}/2026q2/{BETA_CUSIP}?put_call=call")

    assert "Test Capital: ISSUER" in page and DISCLAIMER in page
    assert "40,000" in page and "4,000,000 USD" in page and "100% of the long equity" in page
    assert '<td class="num">30,000 SH</td>' in page and '<td class="num">10,000 SH</td>' in page
    assert "2 lines of the information table added up" in page
    assert "no report for the quarter before, so no change is shown" in page
    assert "No value on this page has changed" in page and "<script" not in page
    assert f'href="/funds/{MANAGER}?quarter=2026Q2"' in page
    assert "Test Capital: ISSUER (CALL)" in option and "not weighted" in option


@pytest.mark.parametrize(
    ("path", "status", "text"),
    [
        (f"/proof/funds/{MANAGER}/2026Q2/{BETA_CUSIP}", 404, "has no position in 000900219"),
        (f"/proof/funds/{MANAGER}/2026Q2/{ACME_CUSIP}?put_call=SWAP", 400, "PUT or CALL"),
        (f"/proof/funds/1234/2026Q2/{ACME_CUSIP}", 404, "not a curated manager"),
    ],
)
async def test_position_proof_errors_are_pages(
    api: httpx2.AsyncClient, auth: dict[str, str], path: str, status: int, text: str
) -> None:
    response = await api.get(path, headers=auth)

    assert response.status_code == status, response.text
    assert text in response.text
