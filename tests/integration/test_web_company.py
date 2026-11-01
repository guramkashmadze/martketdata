"""E15-S2: the search and the company page (PLAN.md §9.14): identity, class and what applies; the
latest release with the changes from a year earlier; eight quarters of the core metrics with a
chart; recent filings with their labels; the insider section (D-111) and the "held by" section
(D-120). Every value links to its proof page; a fund, a foreign issuer or a company outside the
universe gets a page of what applies, never an error."""

import html
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import ACME, ACME_CUSIP, INSIDER_FORM4, MANAGER, Q3_RELEASE, seed
from web_pages import DISCLAIMER, basic, document

Conn = psycopg.AsyncConnection[Any]


@pytest.fixture
async def auth(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return basic(api_key)


async def _page(api: httpx2.AsyncClient, auth: dict[str, str], path: str) -> str:
    response = await api.get(path, headers=auth)
    assert response.status_code == 200, response.text
    return response.text


async def test_a_company_page_shows_identity_release_history_and_filings(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, "/companies/ACME")

    assert "<h1>Acme Corp <span" in page and DISCLAIMER in page
    assert (
        "US domestic filer" in page and "CIK 1001" in page and "3571 Electronic computers" in page
    )
    assert "fiscal year ends December 31" in page
    assert 'href="/companies/ACME-PA">ACME-PA</a> (preferred)' in page
    # The latest release, whatever its status: its revenue failed verification, so it is missing
    # with why, never estimated.
    assert f'href="/proof/earnings/{Q3_RELEASE}"' in page
    assert '<span class="failed">failed</span>' in page
    assert "Not served: not in the source" in page
    # Twelve months and the quarters, exact in USD millions, each linked to its proof.
    assert '<a class="v" href="/proof/metrics/ACME/revenue/TTM/2026-06-30">0.0038</a>' in page
    assert '<a class="v" href="/proof/metrics/ACME/net_margin/TTM/2026-06-30">10.5%</a>' in page
    assert '<a class="v" href="/proof/metrics/ACME/revenue/Q/2026-03-31">0.0009</a>' in page
    assert '<a class="v" href="/proof/metrics/ACME/revenue/Q/2026-06-30">0.001</a>' in page
    assert "Q1 2026" in page and "Q2 2026" in page
    # Filings with their labels, without the prospectus.
    assert "<strong>Quarterly or annual results</strong>" in page
    assert "424B2" not in page


async def test_the_history_chart_reads_exact_values_from_the_page(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = document(await _page(api, auth, "/companies/ACME"))

    (block,) = page.xpath("//script[@id='history-data']/text()")
    chart = json.loads(block)
    (revenue,) = [series for series in chart["series"] if series["metric"] == "revenue"]
    assert chart["labels"] == ["Q1 2026", "Q2 2026"]
    assert revenue["values"] == ["0.0009", "0.001"] and revenue["unit"] == "USD millions"
    assert revenue["proof"] == [
        "/proof/metrics/ACME/revenue/Q/2026-03-31",
        "/proof/metrics/ACME/revenue/Q/2026-06-30",
    ]
    scripts = page.xpath("//script/@src")
    assert any("chart.js-4.5.1" in src for src in scripts)
    assert any(src.startswith("/static/app.js?v=") for src in scripts)
    assert page.xpath("//figure[@data-chart]/@hidden")  # shown by the script only
    assert page.xpath("//canvas/@role") == ["img"] and page.xpath("//label[@for='chart-metric']")


async def test_the_insider_section_sums_trades_and_links_each_to_its_form_4(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, "/companies/ACME")
    every_code = await _page(api, auth, "/companies/ACME?days=365&codes=all")

    trade = f"/proof/insiders/{INSIDER_FORM4}#line-1"
    # Lines 1 and 2 are one sale: 1,500 shares at 50.5 on average, 75,750 USD.
    for number in ("1,500", "50.5", "75,750", "98,500"):
        assert f'<a class="v" href="{trade}">{number}</a>' in page
    assert "Roe Richard" in page and "Officer (Chief Executive Officer)" in page
    assert '<a class="t" href="#insider-trades">1</a>' in page  # one trade, linked to the list
    assert f'class="nowrap" href="/proof/insiders/{INSIDER_FORM4}">' in page
    assert (
        "<strong>Last 90 days</strong>" in page and "<strong>Purchases and sales</strong>" in page
    )
    assert "Gift" not in page  # purchases and sales only, by default
    assert "Gift" in every_code and "<strong>Last 365 days</strong>" in every_code


async def test_the_held_by_section_lists_curated_managers_by_name(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, "/companies/ACME")

    position = f"/proof/funds/{MANAGER}/2026Q2/{ACME_CUSIP}"
    assert f'<a href="/funds/{MANAGER}">Test Capital</a>' in page
    for number in ("40,000", "4,000,000", "100%"):
        assert f'<a class="v" href="{position}">{number}</a>' in page
    assert "2026-08-14" in page and "no report for the quarter before" in page


@pytest.mark.parametrize(
    ("ticker", "shown", "left_out"),
    [
        (
            "FNDX",
            ["Index Fund Trust", "A fund: fundamentals don't apply.", 'id="held-by"'],
            ["Latest earnings release", 'id="insiders"', "Last twelve months"],
        ),
        (
            "FPIX",
            ["Foreign private issuer", "filed since March 2026", 'id="insiders"'],
            ["Latest earnings release", "Last twelve months"],
        ),
        ("OUTC", ["Not in the covered universe."], ["Latest earnings release", 'id="insiders"']),
    ],
)
async def test_funds_and_foreign_issuers_get_a_page_of_what_applies(
    api: httpx2.AsyncClient,
    auth: dict[str, str],
    ticker: str,
    shown: list[str],
    left_out: list[str],
) -> None:
    page = html.unescape(await _page(api, auth, f"/companies/{ticker}"))

    assert '<details class="card coverage" open>' in page
    assert 'class="not_applicable">Not covered</span>' in page
    for text in shown:
        assert text in page
    for text in left_out:
        assert text not in page


async def test_a_ticker_has_one_address(api: httpx2.AsyncClient, auth: dict[str, str]) -> None:
    moved = await api.get("/companies/acme?days=365", headers=auth)
    unknown = await api.get("/companies/NOPE", headers=auth)
    wrong = await api.get("/companies/A%20B%20C", headers=auth)

    assert moved.status_code == 308 and moved.headers["location"] == "/companies/ACME?days=365"
    assert unknown.status_code == 404 and "NOPE" in unknown.text and DISCLAIMER in unknown.text
    assert wrong.status_code == 400 and "text/html" in wrong.headers["content-type"]


async def test_more_trades_load_a_page_at_a_time(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    # 24 more sales on other days: 25 trades, a page of 20 and then 5.
    day = datetime.now(UTC).date() - timedelta(days=20)
    for line_no in range(4, 28):
        await conn.execute(
            "insert into insider_transactions (accession_no, line_no, issuer_cik, is_derivative, "
            "security_title, trade_date, code, acquired_disposed, shares, price, owned_after, "
            "direct_indirect, footnotes, method, extractor_version) values (%s, %s, %s, false, "
            "'Common Stock', %s, 'S', 'D', 10, 50, 1000, 'D', '{}', 'form4_xml', '1')",
            [INSIDER_FORM4, line_no, ACME, day - timedelta(days=line_no)],
        )

    page = document(await _page(api, auth, "/companies/ACME"))
    (more,) = page.xpath("//tr[@id='insider-more']//a")
    rows = await api.get(more.get("hx-get"), headers={**auth, "HX-Request": "true"})
    plain = await api.get(more.get("href"), headers=auth)

    assert len(page.xpath("//h3[@id='insider-trades']/following::table[1]/tbody/tr")) == 21
    assert more.get("hx-target") == "#insider-more" and more.get("hx-swap") == "outerHTML"
    assert page.xpath("//script[contains(@src, 'htmx-2.0.11')]")
    assert rows.status_code == 200 and "<html" not in rows.text
    assert len(document(f"<table>{rows.text}</table>").xpath("//tr")) == 5  # the rest, no more
    assert plain.status_code == 200 and "<html" in plain.text and "Insider trades" in plain.text


async def test_search_opens_a_ticker_and_lists_names(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    by_ticker = await api.get("/search", params={"q": " acme "}, headers=auth)
    by_cik = await api.get("/search", params={"q": str(ACME)}, headers=auth)
    by_name = await _page(api, auth, "/search?q=corp")
    nothing = await _page(api, auth, "/search?q=zzzzzz")
    empty = await _page(api, auth, "/search")
    invalid = await api.get("/search", params={"q": "%%%"}, headers=auth)

    assert by_ticker.status_code == 303 and by_ticker.headers["location"] == "/companies/ACME"
    assert by_cik.status_code == 303 and by_cik.headers["location"] == "/companies/ACME"
    assert 'href="/companies/ACME">ACME</a>' in by_name and "Outside Corp" in by_name
    assert "Not in the covered universe." in by_name
    assert "No company with a current ticker matches" in nothing
    assert "Type a ticker, a company name or a CIK" in empty
    assert invalid.status_code == 400 and "no letter or digit" in invalid.text


async def test_the_home_page_lists_the_covered_companies_by_class(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = await _page(api, auth, "/")

    sections = re.findall(r"<h3>([^<]+) <span", page)
    assert sections == ["US domestic filers", "Foreign private issuers", "Funds"]
    assert 'href="/companies/ACME">ACME</a>' in page and "ACME-PA" not in page
    assert 'href="/companies/FNDX">FNDX</a>' in page and "OUTC" not in page
