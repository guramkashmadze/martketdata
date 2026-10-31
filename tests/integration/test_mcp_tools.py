"""E6-S2: the six tools of PLAN.md §10.3, called in-process through an MCP client. Each answers
from the same service function as the API (D-108): the compact tools with the API's envelope
less its list of flags, each value with its short proof and flags, every filing it reads in
`sources`; latest_earnings with the API's own answer. Arguments are checked with clear
messages; lists stop at 20 filings and calls at 16 metrics and 40 values (25 in a comparison);
funds, foreign issuers and companies outside the universe answer not_applicable; answers stay
under 8 KB on Apple's real filings."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import (
    ACME,
    BETA,
    BETA_10Q,
    OLD,
    OLD_Q1_10Q,
    OLD_Q1_RELEASE,
    PROSPECTUS,
    Q2_10Q,
    Q2_RELEASE,
    Q3_RELEASE,
    metric,
    seed,
)
from apple_data import RELEASES, load_apple
from conftest import LoadMaster
from fastapi import FastAPI
from mcp import Client
from mcp_client import connected, parsed, text_of

from marketdata.mcp_server.tools import HEADLINE
from marketdata.normalize.metric_catalog import UNITS
from marketdata.service.served import DISCLAIMER

Conn = psycopg.AsyncConnection[Any]
TOOLS = {
    "lookup_company",
    "company_snapshot",
    "get_financials",
    "recent_filings",
    "compare_companies",
    "latest_earnings",
}
MAX_ANSWER = 8 * 1024  # bytes (§10.3)


async def _call(client: Client, tool: str, arguments: dict[str, Any]) -> Any:
    result = await client.call_tool(tool, arguments)
    assert result.is_error is False, text_of(result)
    return parsed(result)


async def _error(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, str]:
    result = await client.call_tool(tool, arguments)
    assert result.is_error is True
    error: dict[str, str] = parsed(result)["error"]
    return error


async def _rest(api: httpx2.AsyncClient, key: str, path: str, **params: str) -> Any:
    response = await api.get(path, params=params, headers={"X-API-Key": key})
    assert response.status_code == 200, response.text
    return response.json(parse_float=Decimal)


def _fiscal(row: dict[str, Any]) -> str:
    year, period = row["fiscal_year"], row["fiscal_period"]
    return f"FY{year}" if period == "FY" else f"FY{year} {period}"


def _series(metric: str, unit: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """API metric rows as get_financials and the snapshot write them: the concept once when
    every value shares it."""
    concepts = {row["proof"]["concept"] for row in rows}
    concept = concepts.pop() if len(concepts) == 1 else None
    values = []
    for row in rows:
        value = {
            "period_end": row["period_end"],
            "fiscal": _fiscal(row),
            "value": row["value"],
            "method": row["proof"]["method"],
            "concept": row["proof"]["concept"],
            "accession_no": row["proof"]["accession_no"],
            "flags": row["quality_flags"],
        }
        if concept is not None or value["concept"] is None:
            del value["concept"]
        values.append(value)
    return {"metric": metric, "unit": unit, "concept": concept, "values": values}


def _compared(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "period_end": row["period_end"],
        "value": row["value"],
        "method": row["proof"]["method"],
        "concept": row["proof"]["concept"],
        "accession_no": row["proof"]["accession_no"],
        "flags": row["quality_flags"],
    }


def _same_envelope(answer: dict[str, Any], rest: dict[str, Any]) -> None:
    """A compact answer keeps the API's envelope but its list of flags."""
    assert set(answer) == {"data", "sources", "as_of", "disclaimer"}
    for key in ("sources", "as_of", "disclaimer"):
        assert answer[key] == rest[key], key


async def test_the_six_tools_are_listed_with_their_arguments(
    api_app: FastAPI, api_key: str
) -> None:
    async with connected(api_app, api_key) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(listed) == TOOLS
    financials = listed["get_financials"].input_schema["properties"]
    assert financials["period"]["enum"] == ["Q", "FY", "TTM"]
    assert (financials["limit"]["minimum"], financials["limit"]["maximum"]) == (1, 12)
    assert "free_cash_flow" in financials["metrics"]["description"]
    tickers = listed["compare_companies"].input_schema["properties"]["tickers"]
    assert (tickers["minItems"], tickers["maxItems"]) == (2, 5)
    days = listed["recent_filings"].input_schema["properties"]["days"]
    assert (days["default"], days["maximum"]) == (90, 365)
    for tool in listed.values():
        assert "$ref" not in str(tool.input_schema)  # written inline for every client


async def test_company_snapshot_is_the_apis_in_short(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        answer = await _call(client, "company_snapshot", {"ticker": "acme"})
    rest = await _rest(api, api_key, "/v1/companies/acme")

    data, full = answer["data"], rest["data"]
    assert data["company"] == full["company"]
    assert data["latest_period"] == full["latest_period"]
    assert data["not_reported"] == full["not_reported"]
    assert data["ttm"] == [_series(row["metric"], row["unit"], [row]) for row in full["ttm"]]
    ttm = {series["metric"]: series for series in data["ttm"]}
    assert ttm["revenue"]["concept"] == "Revenues"
    assert ttm["revenue"]["values"] == [
        {
            "period_end": "2026-06-30",
            "fiscal": "FY2026 Q2",
            "value": Decimal(3800),
            "method": "derived_ttm",
            "accession_no": Q2_10Q,
            "flags": [],
        }
    ]
    gap = ttm["operating_cash_flow"]["values"][0]
    assert (gap["value"], gap["flags"]) == (None, ["ttm_gap"])
    release = data["latest_release"]  # the newest release, though it failed (D-097)
    assert (release["accession_no"], release["status"]) == (Q3_RELEASE, "failed")
    assert release["facts"][0]["value"] is None and release["facts"][0]["reasons"]
    assert [filing["accession_no"] for filing in data["recent_filings"]] == [
        filing["accession_no"] for filing in full["recent_filings"]
    ]
    _same_envelope(answer, rest)


async def test_get_financials_is_the_apis_with_short_proofs(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)
    # A value with more digits than a float keeps, from another concept.
    await metric(
        conn,
        ACME,
        "revenue",
        "Q",
        "2025-12-31",
        "123456789012345678.25",
        accession=Q2_10Q,
        fiscal=(2025, "Q4"),
        filed="2026-08-05",
        concept="SalesRevenueNet",
    )
    names = ["revenue", "net_income", "operating_cash_flow", "net_margin"]

    async with connected(api_app, api_key) as client:
        quarters = await _call(client, "get_financials", {"ticker": "ACME", "metrics": ["revenue"]})
        ttm = await _call(
            client, "get_financials", {"ticker": "ACME", "metrics": names, "period": "TTM"}
        )
        default = await _call(client, "get_financials", {"ticker": "ACME"})
    rest = await _rest(
        api, api_key, "/v1/companies/ACME/financials", metrics=",".join(names), period="TTM"
    )

    (revenue,) = quarters["data"]["metrics"]
    # Two concepts in the series: each value names its own. Newest first; the first quarter of
    # 2026 is the predecessor's (D-068).
    assert (revenue["metric"], revenue["unit"], revenue["concept"]) == ("revenue", "USD", None)
    assert [
        (each["period_end"], each["fiscal"], each["value"], each["concept"], each["accession_no"])
        for each in revenue["values"]
    ] == [
        ("2026-06-30", "FY2026 Q2", Decimal(1000), "Revenues", Q2_10Q),
        ("2026-03-31", "FY2026 Q1", Decimal(900), "Revenues", OLD_Q1_10Q),
        ("2025-12-31", "FY2025 Q4", Decimal("123456789012345678.25"), "SalesRevenueNet", Q2_10Q),
    ]
    assert {source["accession_no"] for source in quarters["sources"]} == {Q2_10Q, OLD_Q1_10Q}
    old = next(source for source in quarters["sources"] if source["accession_no"] == OLD_Q1_10Q)
    assert f"/data/{OLD}/" in old["url"]
    by_metric: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    for row in rest["data"]["rows"]:
        by_metric[row["metric"]].append(row)
    units = {"revenue": "USD", "net_income": "USD", "operating_cash_flow": "USD"}
    assert ttm["data"]["metrics"] == [
        _series(name, units.get(name, "percent"), rows) for name, rows in by_metric.items()
    ]
    _same_envelope(ttm, rest)
    assert ttm["data"]["period_type"] == "TTM"
    assert [series["metric"] for series in default["data"]["metrics"]] == list(HEADLINE)
    assert default["data"]["period_type"] == "Q"


async def test_recent_filings_are_reports_and_stakes_unless_a_form_is_asked_for(
    api_app: FastAPI, api_key: str, conn: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(conn)
    monkeypatch.setattr("marketdata.mcp_server.tools.today", lambda: date(2026, 10, 31))

    async with connected(api_app, api_key) as client:
        recent = await _call(client, "recent_filings", {"ticker": "ACME"})
        year = await _call(client, "recent_filings", {"ticker": "ACME", "days": 365})
        notes = await _call(client, "recent_filings", {"ticker": "ACME", "form": "424b2"})

    # 90 days: filed on or after 2026-08-02; the prospectus isn't a report.
    assert recent["data"]["since"] == "2026-08-02"
    assert [each["accession_no"] for each in recent["data"]["filings"]] == [Q3_RELEASE, Q2_10Q]
    release = recent["data"]["filings"][0]
    assert release["form"] == "8-K"
    assert release["url"].endswith(f"{Q3_RELEASE}-index.htm")
    events = [
        (each["item_or_form"], each["label"], each["importance"]) for each in release["events"]
    ]
    assert events == [("2.02", "Quarterly or annual results", "high")]  # 9.01 is hidden (§9.6)
    assert recent["data"]["more"] is False
    assert recent["sources"] == []  # each filing carries its own link
    assert [each["accession_no"] for each in year["data"]["filings"]] == [
        Q3_RELEASE,
        Q2_10Q,
        Q2_RELEASE,
        OLD_Q1_10Q,  # the predecessor's
        OLD_Q1_RELEASE,
    ]
    assert [each["accession_no"] for each in notes["data"]["filings"]] == [PROSPECTUS]
    assert notes["data"]["form"] == "424b2"


async def test_recent_filings_stop_at_20(
    api_app: FastAPI, api_key: str, conn: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(conn)
    monkeypatch.setattr("marketdata.mcp_server.tools.today", lambda: date(2026, 10, 31))
    for day in range(1, 26):
        await conn.execute(
            "insert into filings (accession_no, cik, form, filed_at, items_8k) values "
            "(%s, %s, '8-K', %s, '{8.01}')",
            [f"0002002000-26-1000{day:02d}", BETA, date(2026, 10, day)],
        )

    async with connected(api_app, api_key) as client:
        answer = await _call(client, "recent_filings", {"ticker": "BETA", "days": 30})

    assert len(answer["data"]["filings"]) == 20
    assert answer["data"]["filings"][0]["filed_at"] == "2026-10-25"
    assert answer["data"]["more"] is True


async def test_compare_companies_is_the_apis_with_short_proofs(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        answer = await _call(
            client,
            "compare_companies",
            {"tickers": ["acme", "BETA"], "metrics": ["revenue", "net_income"]},
        )
    rest = await _rest(
        api, api_key, "/v1/compare", tickers="acme,BETA", metrics="revenue,net_income"
    )

    data = answer["data"]
    assert (data["period_type"], data["companies"]) == ("TTM", rest["data"]["companies"])
    assert data["metrics"] == [
        {
            "metric": full["metric"],
            "unit": full["unit"],
            "values": {ticker: _compared(row) for ticker, row in full["values"].items()},
        }
        for full in rest["data"]["metrics"]
    ]
    revenue = data["metrics"][0]["values"]
    assert (revenue["ACME"]["value"], revenue["BETA"]["value"]) == (Decimal(3800), Decimal(5000))
    assert revenue["BETA"]["flags"] == ["outlier_jump"]
    assert data["metrics"][1]["values"]["BETA"] is None  # no filing of BETA reports it
    assert {source["accession_no"] for source in answer["sources"]} >= {Q2_10Q, BETA_10Q}
    _same_envelope(answer, rest)


async def test_latest_earnings_is_the_apis_release_with_its_full_proof(
    api_app: FastAPI, api: httpx2.AsyncClient, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        answer = await _call(client, "latest_earnings", {"ticker": "ACME"})
    rest = await _rest(api, api_key, "/v1/companies/ACME/earnings/latest")

    assert answer == rest
    assert answer["disclaimer"] == DISCLAIMER
    assert answer["data"]["release"]["status"] == "failed"  # the newest, whatever its status


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("get_financials", {"ticker": "ACME", "limit": 13}, "limit: Input should be less than"),
        ("get_financials", {"ticker": "ACME", "period": "W"}, "period: Input should be 'Q'"),
        ("get_financials", {"ticker": "ACME", "metrics": ["ebitda"]}, "unknown metric 'ebitda'"),
        (
            "get_financials",
            {"ticker": "ACME", "metrics": list(HEADLINE), "limit": 9},
            "5 metrics x 9 periods is 45 values; one call answers at most 40",
        ),
        (
            "get_financials",
            {"ticker": "ACME", "metrics": list(UNITS)[:17]},
            "17 metrics are asked for; one call answers at most 16",
        ),
        ("get_financials", {"ticker": "AC ME"}, "'AC ME' is not a ticker"),
        ("compare_companies", {"tickers": ["ACME"]}, "tickers: List should have at least 2"),
        (
            "compare_companies",
            {"tickers": ["ACME", "ACME-PA"]},
            "ACME and ACME-PA are the same company",
        ),
        (
            "compare_companies",
            {
                "tickers": ["ACME", "BETA", "ACME-PA", "OUTC", "FPIX"],
                "metrics": [*HEADLINE, "cash"],
            },
            "5 companies x 6 metrics is 30 values; one call answers at most 25",
        ),
        ("recent_filings", {"ticker": "ACME", "days": 366}, "days: Input should be less than"),
        ("company_snapshot", {}, "ticker: Field required"),
    ],
)
async def test_arguments_are_checked_with_clear_messages(
    api_app: FastAPI,
    api_key: str,
    conn: Conn,
    tool: str,
    arguments: dict[str, Any],
    message: str,
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        error = await _error(client, tool, arguments)

    assert error["code"] == "invalid_parameter"
    assert message in error["message"]
    cur = await conn.execute("select method, path, status from api_requests")
    assert await cur.fetchall() == [("MCP", tool, 400)]


async def test_an_unknown_ticker_is_not_found(api_app: FastAPI, api_key: str, conn: Conn) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        error = await _error(client, "latest_earnings", {"ticker": "ZZZZ"})

    assert error["code"] == "not_found"
    assert "ZZZZ" in error["message"]


@pytest.mark.parametrize(
    ("ticker", "reason"),
    [
        ("FNDX", "A fund"),
        ("FPIX", "A foreign private issuer"),
        ("OUTC", "Not in the covered universe"),
    ],
)
async def test_data_that_doesnt_apply_says_why(
    api_app: FastAPI, api_key: str, conn: Conn, ticker: str, reason: str
) -> None:
    await seed(conn)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("company_snapshot", {"ticker": ticker}),
        ("get_financials", {"ticker": ticker}),
        ("latest_earnings", {"ticker": ticker}),
        ("compare_companies", {"tickers": ["ACME", ticker]}),
    ]

    async with connected(api_app, api_key) as client:
        errors = [await _error(client, tool, arguments) for tool, arguments in calls]
        filings = await _call(client, "recent_filings", {"ticker": ticker})

    for error in errors:
        assert error["code"] == "not_applicable"
        assert reason in error["message"]
    assert filings["data"]["ticker"] == ticker  # filings apply to every filer
    cur = await conn.execute("select path, status from api_requests order by id")
    assert await cur.fetchall() == [
        ("company_snapshot", 422),
        ("get_financials", 422),
        ("latest_earnings", 422),
        ("compare_companies", 422),
        ("recent_filings", 200),
    ]


async def test_answers_stay_under_8_kb_on_apples_filings(
    api_app: FastAPI, api_key: str, conn: Conn, load_master: LoadMaster, tmp_path: Path
) -> None:
    await load_apple(conn, load_master, tmp_path)
    four = ["revenue", "net_income", "eps_diluted", "free_cash_flow"]
    calls: list[tuple[str, dict[str, Any]]] = [
        ("lookup_company", {"query": "a"}),
        ("company_snapshot", {"ticker": "AAPL"}),
        ("get_financials", {"ticker": "AAPL"}),
        ("get_financials", {"ticker": "AAPL", "metrics": four, "limit": 10, "period": "TTM"}),
        ("get_financials", {"ticker": "AAPL", "metrics": four[:3], "limit": 12, "period": "FY"}),
        ("recent_filings", {"ticker": "AAPL", "days": 365}),
        ("compare_companies", {"tickers": ["AAPL", "GOOGL"]}),
        ("latest_earnings", {"ticker": "AAPL"}),
    ]

    async with connected(api_app, api_key) as client:
        answers = [await client.call_tool(tool, arguments) for tool, arguments in calls]

    for (tool, arguments), result in zip(calls, answers, strict=True):
        assert result.is_error is False, (tool, text_of(result))
        assert len(text_of(result).encode()) <= MAX_ANSWER, (tool, arguments)
    latest = parsed(answers[-1])["data"]
    assert latest["release"]["accession_no"] == RELEASES[-1]
    assert (latest["facts"][0]["slot"], latest["facts"][0]["value"]) == (
        "revenue",
        Decimal("109417000000"),
    )
    revenue = parsed(answers[2])["data"]["metrics"][0]
    assert revenue["metric"] == "revenue" and len(revenue["values"]) == 8
    assert revenue["values"][0]["value"] == Decimal("109417000000")  # equals the release
    assert revenue["values"][0]["fiscal"] == "FY2026 Q3"
