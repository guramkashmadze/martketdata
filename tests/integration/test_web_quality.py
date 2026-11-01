"""E15-S5: the web UI tested like the API (PLAN.md §9.14). Every page renders on seeded data with
the disclaimer, loads nothing from another site and passes basic accessibility checks (a
language, one h1 and no skipped heading level, labelled fields, table headers with a scope,
links with text, a named chart, a skip link). Every number on the company, compare, fund and
insider pages links to its proof; every table scrolls inside its own box, so a page never
scrolls sideways at phone width."""

import re
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import ACME_CUSIP, INSIDER_FORM4, MANAGER, Q2_RELEASE, seed
from lxml import html
from web_pages import DISCLAIMER, basic, document, inline_scripts, loaded

Conn = psycopg.AsyncConnection[Any]

PAGES = [
    "/",
    "/search?q=corp",
    "/companies/ACME",
    "/companies/FNDX",
    "/companies/FPIX",
    "/companies/ACME/insiders?days=365&codes=all",
    "/compare?tickers=ACME,BETA",
    "/funds",
    f"/funds/{MANAGER}",
    "/status",
    f"/proof/earnings/{Q2_RELEASE}",
    "/proof/metrics/ACME/revenue/Q/2026-06-30",
    f"/proof/insiders/{INSIDER_FORM4}",
    f"/proof/funds/{MANAGER}/2026Q2/{ACME_CUSIP}",
    "/nothing/here",
]
# Pages whose numbers are served values: each links to its proof page, to the rows it sums on
# the page (#…), or to the SEC filing that is its proof.
VALUE_PAGES = [
    "/companies/ACME",
    "/companies/ACME/insiders?days=365&codes=all",
    "/compare?tickers=ACME,BETA",
    f"/funds/{MANAGER}",
]
PROOF_LINK = ("/proof/", "#", "https://www.sec.gov/")
DATES = re.compile(r"^\d{4}-\d{2}-\d{2}( to \d{4}-\d{2}-\d{2})?$")


@pytest.fixture
async def auth(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return basic(api_key)


def _problems(page: html.HtmlElement) -> list[str]:
    """What a page gets wrong of the basic accessibility checks."""
    found = []
    if page.xpath("/html/@lang") != ["en"]:
        found.append("no language")
    if not "".join(page.xpath("//title/text()")).strip():
        found.append("no title")
    if len(page.xpath("//h1")) != 1:
        found.append("not one h1")
    level = 0
    for heading in page.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        now = int(heading.tag[1])
        if now > level + 1:
            found.append(f"skips to {heading.tag}: {heading.text_content().strip()}")
        level = now
    labelled = set(page.xpath("//label/@for"))
    for field in page.xpath("//input[not(@type='hidden')]|//select|//textarea"):
        if field.get("id") not in labelled and not field.get("aria-label"):
            found.append(f"unlabelled {field.tag} {field.get('name')}")
    found += [
        f"th without scope: {th.text_content().strip()}" for th in page.xpath("//th[not(@scope)]")
    ]
    for link in page.xpath("//a"):
        if not link.text_content().strip() and not link.get("aria-label"):
            found.append(f"link without text: {link.get('href')}")
    for canvas in page.xpath("//canvas"):
        if canvas.get("role") != "img" or not canvas.get("aria-label"):
            found.append("unnamed chart")
    if not page.xpath("//a[@class='skip'][@href='#main']") or not page.xpath("//*[@id='main']"):
        found.append("no skip link")
    if page.xpath("//*[@tabindex > 0]"):
        found.append("a positive tabindex")
    found += [
        "a table outside a scroll box"
        for t in page.xpath("//table[not(ancestor::div[@class='scroll'])]")
    ]
    return found


@pytest.mark.parametrize("path", PAGES)
async def test_every_page_renders_and_passes_the_basic_checks(
    api: httpx2.AsyncClient, auth: dict[str, str], path: str
) -> None:
    response = await api.get(path, headers=auth)

    assert response.status_code == (404 if path == "/nothing/here" else 200), response.text
    assert DISCLAIMER in response.text
    page = document(response.text)
    assert _problems(page) == []
    assert all(url.startswith("/") for url in loaded(page)) and inline_scripts(page) == []
    assert page.xpath("//meta[@name='viewport']/@content") == [
        "width=device-width, initial-scale=1"
    ]
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path", VALUE_PAGES)
async def test_every_number_links_to_its_proof(
    api: httpx2.AsyncClient, auth: dict[str, str], path: str
) -> None:
    page = document((await api.get(path, headers=auth)).text)

    unlinked = []
    numbers = 0
    for cell in page.xpath("//td[contains(concat(' ', @class, ' '), ' num ')]"):
        text = " ".join(cell.text_content().split())
        if not re.search(r"\d", text) or DATES.match(text):
            continue
        numbers += 1
        if not any(href.startswith(PROOF_LINK) for href in cell.xpath(".//a/@href")):
            unlinked.append(text)
    assert numbers and unlinked == []
    for value in page.xpath("//a[contains(concat(' ', @class, ' '), ' v ')]"):
        assert value.get("href").startswith("/proof/"), value.get("href")
