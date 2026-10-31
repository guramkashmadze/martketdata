"""E8-S5: the MCP tool insider_activity (§10.3): the API's insider trades in a compact answer,
purchases and sales by default, at most 12 trades and 8 insiders and under 8 KB, every filing
of the trades listed in `sources`; arguments checked; funds answer not_applicable."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import psycopg
from api_seed import ACME, INSIDER_FORM4, seed
from fastapi import FastAPI
from mcp_client import connected, parsed, text_of

from marketdata.mcp_server.tools import INSIDER_LINES, INSIDER_TRADES

Conn = psycopg.AsyncConnection[Any]
MAX_ANSWER = 8 * 1024


async def test_insider_activity_is_the_apis_in_short(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("insider_activity", {"ticker": "acme"})
    rest = (await api.get("/v1/companies/ACME/insiders", headers={"X-API-Key": api_key})).json(
        parse_float=Decimal
    )

    assert result.is_error is False, text_of(result)
    answer = parsed(result)
    assert set(answer) == {"data", "sources", "as_of", "disclaimer"}
    for key in ("sources", "as_of", "disclaimer"):
        assert answer[key] == rest[key], key
    data = answer["data"]
    assert (data["ticker"], data["codes"], data["totals"]) == (
        "ACME",
        ["P", "S"],
        rest["data"]["totals"],
    )
    [trade] = data["trades"]
    api_trade = rest["data"]["trades"][0]
    assert trade == {
        "trade_date": api_trade["trade_date"],
        "insiders": "Roe Richard",
        "code": "S",
        "security": "Common Stock",
        "shares": Decimal("1500"),
        "average_price": Decimal("50.5000"),
        "value": Decimal("75750.00"),
        "rule_10b5_1": True,
        "accession_no": INSIDER_FORM4,
        "flags": [],
    }
    [insider] = data["insiders"]
    assert (insider["insiders"], insider["roles"], insider["code"], insider["trades"]) == (
        "Roe Richard",
        "director, officer (Chief Executive Officer)",
        "S",
        1,
    )
    assert (data["more_insiders"], data["more_trades"]) == (False, False)
    cur = await conn.execute("select method, path, status from api_requests order by id")
    assert ("MCP", "insider_activity", 200) in await cur.fetchall()


async def test_arguments_and_capabilities_are_checked(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        results = [
            await client.call_tool("insider_activity", arguments)
            for arguments in (
                {"ticker": "ACME", "days": 0},
                {"ticker": "ACME", "codes": ["PS"]},
                {"ticker": "FNDX"},
                {"ticker": "FPIX"},
            )
        ]

    too_few_days, bad_code, fund, foreign = results
    assert too_few_days.is_error and parsed(too_few_days)["error"]["code"] == "invalid_parameter"
    assert parsed(bad_code)["error"]["message"].startswith("codes are Form 4 transaction codes")
    assert parsed(fund)["error"] == {
        "code": "not_applicable",
        "message": "FNDX (Index Fund Trust): A fund: insider Form 4 filings don't apply.",
    }
    assert foreign.is_error is False and parsed(foreign)["data"]["trades"] == []


async def test_a_busy_window_stays_under_8_kb(api_app: FastAPI, api_key: str, conn: Conn) -> None:
    """A heavy filer: 12 insiders selling on 30 days, each a Form 4 with a long footnote."""
    await seed(conn)
    today = datetime.now(UTC).date()
    for n in range(30):
        accession = f"0001001000-26-1{n:05d}"
        owner = 9100 + n % 12
        key = f"raw/sec/{ACME}/{accession}/form4.xml"
        await conn.execute(
            "insert into raw_objects (key, url, sha256, size_bytes) values (%s, %s, %s, 1)",
            [key, f"https://www.sec.gov/Archives/edgar/data/{ACME}/x/form4.xml", "b" * 64],
        )
        await conn.execute(
            "insert into insider_filings (accession_no, issuer_cik, form, filed_at, "
            "period_of_report, owner_ciks, rule_10b5_1, doc_name, raw_key, footnotes, method, "
            "extractor_version) values (%s, %s, '4', %s, %s, %s, true, 'form4.xml', %s, %s, "
            "'form4_xml', '1')",
            [
                accession,
                ACME,
                today,
                today - timedelta(days=n),
                [owner],
                key,
                json.dumps({"F1": "x" * 400}),
            ],
        )
        await conn.execute(
            "insert into insider_owners (accession_no, owner_cik, position, owner_name, "
            "is_director, is_officer, officer_title, is_ten_pct_owner, is_other) values "
            "(%s, %s, 1, %s, false, true, 'Executive Vice President, Chief Financial Officer', "
            "false, false)",
            [accession, owner, f"Insider With A Long Name Number {owner}"],
        )
        for line in range(1, 4):
            await conn.execute(
                "insert into insider_transactions (accession_no, line_no, issuer_cik, "
                "is_derivative, security_title, trade_date, code, acquired_disposed, shares, "
                "price, owned_after, direct_indirect, footnotes, method, extractor_version) "
                "values (%s, %s, %s, false, 'Class A Common Stock', %s, 'S', 'D', 1234.5678, "
                "123.4567, 98765.4321, 'D', %s, 'form4_xml', '1')",
                [accession, line, ACME, today - timedelta(days=n), json.dumps({"price": ["F1"]})],
            )

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("insider_activity", {"ticker": "ACME", "days": 365})

    assert result.is_error is False, text_of(result)
    size = len(text_of(result).encode())
    assert size <= MAX_ANSWER, size
    data = parsed(result)["data"]
    assert (len(data["trades"]), len(data["insiders"])) == (INSIDER_TRADES, INSIDER_LINES)
    assert (data["more_trades"], data["more_insiders"]) == (True, True)
    assert data["totals"][0]["trades"] == 31  # the window's 30 days and the seeded sale
