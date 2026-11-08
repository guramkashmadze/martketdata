"""E16-S5: the MCP tool major_holders (§10.3): the API's 5% holders in a compact answer under 8 KB,
even for a company with many holders; holders and exits by name, each with its filing's link;
the newest old-format filings as links; the description says what it covers and that it is
information, not advice."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
from api_seed import ACME, OLD_13G, STAKE_13D, STAKE_13G, seed
from fastapi import FastAPI
from mcp_client import connected, parsed, text_of
from stake_world import AMD, VANGUARD_AMD, VCM_AMD, ingest, list_filing

from marketdata.mcp_server.tools import STAKE_EXITS, STAKE_HOLDERS, STAKE_OLDER
from marketdata.sec import urls

Conn = psycopg.AsyncConnection[Any]
MAX_ANSWER = 8 * 1024


async def test_major_holders_is_the_apis_in_short(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("major_holders", {"ticker": "acme"})
    rest = (await api.get("/v1/companies/ACME/stakes", headers={"X-API-Key": api_key})).json(
        parse_float=Decimal
    )

    assert result.is_error is False, text_of(result)
    answer = parsed(result)
    assert set(answer) == {"data", "sources", "as_of", "checked_at", "check_overdue", "disclaimer"}
    assert answer["sources"] == []  # each line carries its filing's link
    for key in ("as_of", "checked_at", "check_overdue"):
        assert answer[key] == rest[key], key
    data = answer["data"]
    assert (data["ticker"], data["note"]) == ("ACME", rest["data"]["note"])
    [held] = data["holders"]
    assert held == {
        "holder": "Big Index Advisers",
        "form": "SCHEDULE 13G/A",
        "filed_at": "2026-05-10",
        "event_date": "2026-03-31",
        "ticker": "ACME",
        "shares": Decimal(12500000),
        "percent": Decimal("6.25"),
        "reporting_persons": 1,
        "accession_no": STAKE_13G,
        "url": urls.filing_folder(ACME, STAKE_13G),
        "flags": [],
    }
    [exited] = data["exited"]
    assert (exited["holder"], exited["accession_no"], exited["percent"]) == (
        "Activist Partners LP",
        STAKE_13D,
        Decimal("4.1"),
    )
    assert data["older_filings"] == [
        {"form": "SC 13G", "filed_at": "2024-02-12", "url": urls.filing_index_page(ACME, OLD_13G)}
    ]
    assert (data["more_holders"], data["more_exited"], data["more_older_filings"]) == (
        False,
        False,
        False,
    )


async def test_amd_answers_vanguard_capital_management_from_its_filing(
    api_app: FastAPI, api_key: str, conn: Conn, tmp_path: Path
) -> None:
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (%s, 'ADVANCED MICRO DEVICES INC', 'domestic', true)",
        [AMD],
    )
    await conn.execute(
        "insert into securities (cik, ticker, security_type, valid_from) "
        "values (%s, 'AMD', 'common', '2020-01-01')",
        [AMD],
    )
    for listed in (VANGUARD_AMD, VCM_AMD):
        await list_filing(conn, listed)
        await ingest(conn, tmp_path, listed)

    async with connected(api_app, api_key) as client:
        answer = parsed(await client.call_tool("major_holders", {"ticker": "AMD"}))

    [held] = answer["data"]["holders"]
    assert (held["holder"], held["shares"], held["percent"], held["accession_no"]) == (
        "Vanguard Capital Management",
        Decimal(122092206),
        Decimal("7.48"),
        VCM_AMD.accession_no,
    )
    [exited] = answer["data"]["exited"]
    assert (exited["holder"], exited["shares"]) == ("The Vanguard Group", Decimal(0))


async def _busy(conn: Conn) -> None:
    """A company with more holders, exits and old filings than an answer lists, each with long
    names and class titles."""
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (7007, 'Busy Holdings Incorporated', 'domestic', true)"
    )
    await conn.execute(
        "insert into securities (cik, ticker, security_type, valid_from) "
        "values (7007, 'BUSY', 'common', '2020-01-01')"
    )
    key = "raw/sec/7007/busy.xml"
    await conn.execute(
        "insert into raw_objects (key, url, sha256, size_bytes) values (%s, %s, %s, 1)",
        [key, "https://www.sec.gov/Archives/edgar/data/7007/busy.xml", "2" * 64],
    )
    day = date(2026, 1, 5)
    for n in range(40):
        accession = f"00{8000000 + n:08d}-26-{n:06d}"
        exited = n % 2 == 1
        await conn.execute(
            "insert into stake_filings (accession_no, issuer_cik, form, filed_at, filer_cik, "
            "cusips, class_title, event_date, rules, checks, doc_name, raw_key, method, "
            "extractor_version) values (%s, 7007, 'SCHEDULE 13G/A', %s, %s, '{999999999}', "
            "'Class A Common Stock, par value $0.0001 per share', %s, '{Rule 13d-1(b)}', "
            "'{issuer_matches_listing,form_matches_listing,percent_in_range,"
            "powers_within_amount}', 'primary_doc.xml', %s, 'schedule13_xml', '1')",
            [accession, day + timedelta(days=n), 8000000 + n, day, key],
        )
        await conn.execute(
            "insert into stake_persons (accession_no, position, name, types, amount, percent, "
            "method, extractor_version) values (%s, 1, %s, '{IA}', %s, %s, "
            "'schedule13_xml', '1')",
            [
                accession,
                f"Long Named Global Investment Management Holdings {n:02d} LLC",
                Decimal("123456789.00"),
                Decimal("4.9") if exited else Decimal("12.34"),
            ],
        )
    for n in range(30):
        await conn.execute(
            "insert into filings (accession_no, cik, form, filed_at, file_number) "
            "values (%s, 7007, 'SC 13G/A', %s, '005-99999')",
            [f"00{9000000 + n:08d}-24-{n:06d}", date(2024, 2, 1) - timedelta(days=n)],
        )


async def test_a_busy_company_stays_under_8_kb(api_app: FastAPI, api_key: str, conn: Conn) -> None:
    await _busy(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("major_holders", {"ticker": "BUSY"})

    text = text_of(result)
    assert len(text.encode()) < MAX_ANSWER, len(text.encode())
    data = parsed(result)["data"]
    assert len(data["holders"]) == STAKE_HOLDERS and data["more_holders"] is True
    assert len(data["exited"]) == STAKE_EXITS and data["more_exited"] is True
    assert len(data["older_filings"]) == STAKE_OLDER and data["more_older_filings"] is True
    names = [line["holder"] for line in data["holders"]]
    assert names == sorted(names)  # by name, never by size


async def test_the_description_says_what_it_covers(api_app: FastAPI, api_key: str) -> None:
    async with connected(api_app, api_key) as client:
        [tool] = [t for t in (await client.list_tools()).tools if t.name == "major_holders"]

    description = " ".join((tool.description or "").split())
    assert "more than 5%" in description
    assert "Schedule 13D and 13G" in description
    assert "2024-12-18" in description
    assert "never ranked by size" in description
    assert "not investment advice" in description
