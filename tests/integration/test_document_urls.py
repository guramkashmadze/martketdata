"""D-140: every filing served links its main document as filed, next to the links it had: the
REST filings, the snapshot's latest filings and every envelope's sources, the `filing.event`
webhook's filing, the company page and the proof pages. For a form filed as XML the document is
the XML, not EDGAR's rendering of it; a listing that names no primary document gets null, never
a guessed name. MCP lists give the document in place of the index page (the index page only
without a document), and MCP sources keep the folder alone, so answers stay under 8 KB.

E16's 5% stakes too: each holder's line and source give the XML the stake was read from, and an
old-format filing its HTML or text, the MCP its index page only without one.

The seed's filings name no primary document; each test names some here: ACME's Q2 release and
10-Q, its predecessor's Q1 10-Q, its old-format SC 13G, and a Schedule 13G about ACME, listed
under ACME (the subject, 005-) and under its filer, as EDGAR lists stakes (D-061)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from api_contract import check_envelope
from api_seed import (
    ACME,
    FY2020_10K,
    INSIDER_FORM4,
    MANAGER,
    MANAGER_13F,
    OLD,
    OLD_13G,
    OLD_Q1_10Q,
    OLD_Q1_RELEASE,
    Q2_10Q,
    Q2_RELEASE,
    Q3_RELEASE,
    STAKE_13D,
    STAKE_13G,
    seed,
)
from conftest import SEC_FIXTURES, connect_to, sec_serving
from fastapi import FastAPI
from mcp_client import connected, mcp_sources, parsed, text_of
from web_pages import basic

from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.events import FILING_EVENT, event_object
from marketdata.service.filings import FilingView, recent_filings
from marketdata.service.served import Source

Conn = psycopg.AsyncConnection[Any]
FUBO, DIS = 1484769, 1744489
THIRTEEN_D = "0000950157-25-000917"  # Disney's Schedule 13D about FuboTV
STAKE, HOLDER = "0007007000-26-000001", 7007  # a Schedule 13G about ACME, and its filer
DOCUMENTS = {
    Q2_RELEASE: (ACME, "acme-20260728.htm"),
    Q2_10Q: (ACME, "acme-20260630.htm"),
    OLD_Q1_10Q: (OLD, "old-20260331.htm"),  # the predecessor's listing: its folder (D-068)
    OLD_13G: (ACME, "acme-sc13g.txt"),  # an old-format stake, listed but not read (E16)
}
STAKE_XML = urls.filing_document(ACME, STAKE, "primary_doc.xml")


def _document(accession: str) -> str:
    cik, name = DOCUMENTS[accession]
    return urls.filing_document(cik, accession, name)


@pytest.fixture
async def named(conn: Conn) -> Conn:
    await seed(conn)
    for accession, (cik, name) in DOCUMENTS.items():
        await conn.execute(
            "update filings set primary_doc = %s where accession_no = %s and cik = %s",
            [name, accession, cik],
        )
    await conn.execute(
        "insert into filings (accession_no, cik, form, filed_at, accepted_at, primary_doc, "
        "file_number) values "
        "(%s, %s, 'SCHEDULE 13G', '2026-10-29', '2026-10-29T12:00:00Z', %s, '005-12345'), "
        "(%s, %s, 'SCHEDULE 13G', '2026-10-29', '2026-10-29T12:00:00Z', %s, null)",
        [
            STAKE,
            ACME,
            "xslSCHEDULE_13G_X02/primary_doc.xml",
            STAKE,
            HOLDER,
            "xslSCHEDULE_13G_X02/primary_doc.xml",
        ],
    )
    return conn


async def _rest(api: httpx2.AsyncClient, key: str, path: str, **params: str) -> Any:
    response = await api.get(path, params=params, headers={"X-API-Key": key})
    assert response.status_code == 200, response.text
    body = response.json(parse_float=Decimal)
    check_envelope(body)
    return body


async def test_rest_filings_name_their_main_documents_in_the_listings_folder(
    api: httpx2.AsyncClient, api_key: str, named: Conn
) -> None:
    body = await _rest(api, api_key, "/v1/companies/ACME/filings", limit="10")

    filings = {filing["accession_no"]: filing for filing in body["data"]["filings"]}
    assert filings[Q2_10Q]["document_url"] == _document(Q2_10Q)
    assert filings[OLD_Q1_10Q]["document_url"] == _document(OLD_Q1_10Q)
    # The 13G as filed: the XML, under ACME's listing (the one served), not the rendering.
    assert filings[STAKE]["document_url"] == STAKE_XML
    assert filings[STAKE]["url"] == urls.filing_index_page(ACME, STAKE)
    # No primary document in the SEC's list: null, the index page as before.
    assert filings[Q3_RELEASE]["document_url"] is None
    assert filings[Q3_RELEASE]["url"] == urls.filing_index_page(ACME, Q3_RELEASE)
    # Each filing's source names the same document, in the folder its url names.
    sources = {source["accession_no"]: source for source in body["sources"]}
    assert sources[STAKE] == {
        "accession_no": STAKE,
        "form": "SCHEDULE 13G",
        "filed_at": "2026-10-29",
        "url": urls.filing_folder(ACME, STAKE),
        "document_url": STAKE_XML,
    }
    assert sources[OLD_Q1_10Q]["document_url"] == _document(OLD_Q1_10Q)
    assert sources[OLD_Q1_RELEASE]["document_url"] is None


async def test_the_snapshot_and_every_envelopes_sources_name_documents(
    api: httpx2.AsyncClient, api_key: str, named: Conn
) -> None:
    snapshot = await _rest(api, api_key, "/v1/companies/ACME")
    years = await _rest(
        api, api_key, "/v1/companies/ACME/financials", metrics="revenue", period="FY"
    )
    trades = await _rest(api, api_key, "/v1/companies/ACME/insiders")
    holdings = await _rest(api, api_key, f"/v1/funds/{MANAGER}/holdings")

    latest = snapshot["data"]["recent_filings"]
    assert [(f["accession_no"], f["document_url"]) for f in latest] == [
        (STAKE, STAKE_XML),
        (Q3_RELEASE, None),
        (Q2_10Q, _document(Q2_10Q)),
    ]
    sources = {source["accession_no"]: source for source in snapshot["sources"]}
    assert sources[Q2_10Q]["document_url"] == _document(Q2_10Q)
    # A filing older than the filings backfill is known from its facts only: no document.
    [old] = years["sources"]
    assert (old["accession_no"], old["document_url"]) == (FY2020_10K, None)
    # A Form 4's source is the XML its lines were read from, a 13F's its cover.
    assert [source["document_url"] for source in trades["sources"]] == [
        urls.filing_document(ACME, INSIDER_FORM4, "form4.xml")
    ]
    assert [source["document_url"] for source in holdings["sources"]] == [
        urls.filing_document(MANAGER, MANAGER_13F, "primary_doc.xml")
    ]


async def test_mcp_lists_give_the_document_in_place_of_the_index_page(
    api_app: FastAPI,
    api: httpx2.AsyncClient,
    api_key: str,
    named: Conn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("marketdata.mcp_server.tools.today", lambda: date(2026, 10, 31))

    async with connected(api_app, api_key) as client:
        recent = await client.call_tool("recent_filings", {"ticker": "ACME", "days": 365})
        thirteen_g = await client.call_tool(
            "recent_filings", {"ticker": "ACME", "form": "SCHEDULE 13G", "days": 365}
        )
        snapshot = await client.call_tool("company_snapshot", {"ticker": "ACME"})
    rest = await _rest(api, api_key, "/v1/companies/ACME")

    filings = {filing["accession_no"]: filing for filing in parsed(recent)["data"]["filings"]}
    assert filings[Q2_10Q] == {
        "accession_no": Q2_10Q,
        "form": "10-Q",
        "filed_at": "2026-08-05",
        "period_of_report": "2026-06-30",
        "document_url": _document(Q2_10Q),
        "events": [],
    }
    # Without a document, the index page: a filing always has one link.
    release = filings[Q3_RELEASE]
    assert release["document_url"] is None
    assert release["url"] == urls.filing_index_page(ACME, Q3_RELEASE)
    assert [f["document_url"] for f in parsed(thirteen_g)["data"]["filings"]] == [STAKE_XML]
    assert parsed(recent)["sources"] == []
    # The snapshot's latest filings likewise; its sources keep their folders alone.
    answer = parsed(snapshot)
    assert [f.get("url") for f in answer["data"]["recent_filings"]] == [
        None,
        urls.filing_index_page(ACME, Q3_RELEASE),
        None,
    ]
    assert answer["sources"] == mcp_sources(rest)
    assert all(
        set(source) == {"accession_no", "form", "filed_at", "url"} for source in answer["sources"]
    )
    for result in (recent, thirteen_g, snapshot):
        assert len(text_of(result).encode()) <= 8 * 1024


async def test_mcp_sources_leave_the_document_out(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, named: Conn
) -> None:
    """The insider seed dates its trades from today, so today stays as it is here."""
    async with connected(api_app, api_key) as client:
        trades = parsed(await client.call_tool("insider_activity", {"ticker": "ACME"}))
    rest = await _rest(api, api_key, "/v1/companies/ACME/insiders")

    assert [source["document_url"] for source in rest["sources"]] == [
        urls.filing_document(ACME, INSIDER_FORM4, "form4.xml")
    ]
    assert trades["sources"] == mcp_sources(rest)
    assert all("document_url" not in source for source in trades["sources"])


async def test_stakes_link_each_filings_document(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, named: Conn
) -> None:
    rest = await _rest(api, api_key, "/v1/companies/ACME/stakes")
    async with connected(api_app, api_key) as client:
        result = await client.call_tool("major_holders", {"ticker": "ACME"})
    page = (await api.get("/companies/ACME", headers=basic(api_key))).text

    held_xml = urls.filing_document(ACME, STAKE_13G, "primary_doc.xml")
    exited_xml = urls.filing_document(ACME, STAKE_13D, "primary_doc.xml")
    # REST: each holder's proof and source name the XML read; an old filing its document.
    [held] = rest["data"]["holders"]
    assert held["proof"]["document_url"] == held_xml
    assert {s["accession_no"]: s["document_url"] for s in rest["sources"]} == {
        STAKE_13G: held_xml,
        STAKE_13D: exited_xml,
    }
    [older] = rest["data"]["older_filings"]
    assert (older["url"], older["document_url"]) == (
        urls.filing_index_page(ACME, OLD_13G),
        _document(OLD_13G),
    )
    # MCP: the XML in place of the folder, an old filing's document in place of its index page.
    data = parsed(result)["data"]
    [line] = data["holders"]
    [exit_line] = data["exited"]
    assert (line["document_url"], exit_line["document_url"]) == (held_xml, exited_xml)
    assert "url" not in line and "url" not in exit_line
    assert data["older_filings"] == [
        {"form": "SC 13G", "filed_at": "2024-02-12", "document_url": _document(OLD_13G)}
    ]
    assert len(text_of(result).encode()) <= 8 * 1024
    # The company page: the form opens the old filing's index page, the file name its document.
    assert f'<a href="{_document(OLD_13G)}">acme-sc13g.txt</a>' in page


@pytest.fixture
async def watched(conn: Conn, tmp_path: Path) -> Conn:
    """FuboTV's and Disney's listings from their submissions files (fixtures), as the watcher
    stores them: Disney's 13D on FuboTV is on both (test_recent_filings.py)."""
    routes: dict[str, bytes | int] = {
        urls.submissions(cik): (SEC_FIXTURES / "submissions" / f"{ticker}.json").read_bytes()
        for cik, ticker in ((FUBO, "FUBO"), (DIS, "DIS"))
    }
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        watcher = FilingsWatcher(fetcher, connect_to(conn), frozenset())
        await watcher.watch(FUBO)
        await watcher.watch(DIS)
    for cik, name, ticker in ((FUBO, "FuboTV Inc.", "FUBO"), (DIS, "Walt Disney Co", "DIS")):
        await conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) "
            "values (%s, %s, 'domestic', true) on conflict (cik) do nothing",
            [cik, name],
        )
        await conn.execute(
            "insert into securities (cik, ticker, valid_from) values (%s, %s, '2026-10-25')",
            [cik, ticker],
        )
    return conn


@pytest.mark.parametrize(
    ("ticker", "form", "accession", "file"),
    [
        ("FUBO", "8-K", "0001493152-25-020163", "form8-k.htm"),
        ("FUBO", "10-Q", "0001628280-26-053535", "fubo-20260630.htm"),
        # Filed as XML: listed as xslF345X06/ownership.xml, xsl144X01/primary_doc.xml ...
        ("FUBO", "4", "0001493152-26-039739", "ownership.xml"),
        ("FUBO", "3", "0001493152-26-033079", "ownership.xml"),
        ("FUBO", "144", "0001959173-26-005303", "primary_doc.xml"),
        ("FUBO", "SCHEDULE 13G/A", "0001104659-26-061197", "primary_doc.xml"),
        # Disney's 13D about FuboTV is listed under both, and each listing names its own
        # folder; Disney's own stake is listed only without a form (D-139).
        ("FUBO", "SCHEDULE 13D", THIRTEEN_D, "primary_doc.xml"),
        ("DIS", None, THIRTEEN_D, "primary_doc.xml"),
    ],
)
async def test_the_secs_listings_give_each_document_as_filed(
    watched: Conn, ticker: str, form: str | None, accession: str, file: str
) -> None:
    filing, source = await _listed(watched, ticker, form, accession)

    assert filing.document_url == urls.filing_document(filing.cik, accession, file)
    assert filing.cik == (DIS if ticker == "DIS" else FUBO)
    assert source.document_url == filing.document_url


async def _listed(
    conn: Conn, ticker: str, form: str | None, accession: str
) -> tuple[FilingView, Source]:
    """The filing as the company's filings list serves it, page after page, with its source."""
    cursor = None
    while True:
        served = await recent_filings(conn, ticker, form=form, limit=100, cursor=cursor)
        for filing in served.data.filings:
            if filing.accession_no == accession:
                [source] = [s for s in served.sources if s.accession_no == accession]
                return filing, source
        cursor = served.data.next_cursor
        assert cursor is not None, f"{accession} isn't listed for {ticker}"


async def test_a_filing_event_carries_the_document(named: Conn) -> None:
    announced = await event_object(named, FILING_EVENT, f"{STAKE}:{ACME}", {})

    filing = announced.object.model_dump()["filing"]
    assert (filing["url"], filing["document_url"]) == (
        urls.filing_index_page(ACME, STAKE),
        STAKE_XML,
    )


async def test_the_company_and_proof_pages_link_each_document(
    api: httpx2.AsyncClient, api_key: str, named: Conn
) -> None:
    auth = basic(api_key)
    company = (await api.get("/companies/ACME", headers=auth)).text
    metric = (await api.get("/proof/metrics/ACME/revenue/Q/2026-06-30", headers=auth)).text
    release = (await api.get(f"/proof/earnings/{Q2_RELEASE}", headers=auth)).text

    # The form opens the index page as before; the document opens the filing itself.
    assert f'<a href="{urls.filing_index_page(ACME, Q2_10Q)}">10-Q</a>' in company
    assert f'<a href="{_document(Q2_10Q)}">acme-20260630.htm</a>' in company
    assert f'<a href="{STAKE_XML}">primary_doc.xml</a>' in company
    assert "/xsl" not in company.lower()
    assert '<th scope="col">Document</th>' in company
    # The proof pages' filings: each accession opens the folder, its document the filing.
    assert f'<a href="{_document(Q2_10Q)}">acme-20260630.htm</a>' in metric
    assert f'<a href="{_document(Q2_RELEASE)}">acme-20260728.htm</a>' in release
    assert f'<a href="{urls.filing_folder(ACME, Q2_RELEASE)}">{Q2_RELEASE}</a>' in release
