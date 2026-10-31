"""E6-S1: the MCP server at /mcp. A client connects over Streamable HTTP and lists the tools;
requests without a valid `api`-scope key are refused with the §10.1 error envelope; each tool
call writes an `api_requests` row (method MCP, path = the tool's name) with its outcome's
status; protocol messages aren't logged; only this machine's Host is accepted. Also
lookup_company, the first tool (D-109)."""

from typing import Any

import psycopg
import pytest
from api_seed import ACME, seed
from fastapi import FastAPI
from mcp import Client
from mcp_client import (
    POST_HEADERS,
    connected,
    http,
    initialize,
    parsed,
    serving,
    session,
    text_of,
    tool_call,
)

from marketdata.service.clients import authenticate, create_key, revoke_key
from marketdata.service.served import DISCLAIMER

Conn = psycopg.AsyncConnection[Any]


async def _logged(conn: Conn) -> list[tuple[Any, ...]]:
    cur = await conn.execute(
        "select key_id, method, path, status, duration_ms >= 0 from api_requests order by id"
    )
    return await cur.fetchall()


async def _key_id(conn: Conn, key: str) -> int:
    return (await authenticate(conn, key)).key_id


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_a_client_connects_and_lists_the_tools(
    api_app: FastAPI, api_key: str, conn: Conn, mode: str
) -> None:
    async with connected(api_app, api_key, mode=mode) as client:
        listed = await client.list_tools()
        info, instructions = client.server_info, client.instructions

    if mode == "legacy":  # the initialize handshake carries the server's name and instructions
        assert info is not None and info.name == "marketdata"
        assert "not investment advice" in (instructions or "")
    tools = {tool.name: tool for tool in listed.tools}
    assert "lookup_company" in tools
    for tool in tools.values():
        assert "not investment advice" in (tool.description or "")
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
    assert tools["lookup_company"].input_schema["required"] == ["query"]
    # Protocol messages are checked for the key but not logged.
    assert await _logged(conn) == []


async def test_requests_without_a_valid_key_are_refused_and_logged(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    revoked = await create_key(conn, "test client")
    await revoke_key(conn, revoked.stored.key_id)
    review = await create_key(conn, "test client", scope="review")

    async with serving(api_app), http(api_app) as client:
        answers = [
            await client.post("/mcp", json=initialize(), headers=POST_HEADERS | extra)
            for extra in (
                {},
                {"X-API-Key": "mdk_not-a-key"},
                {"X-API-Key": revoked.key},
                {"X-API-Key": review.key},
            )
        ]

    assert [answer.status_code for answer in answers] == [401, 401, 401, 403]
    assert [answer.json()["error"]["code"] for answer in answers] == [
        "unauthorized",
        "unauthorized",
        "unauthorized",
        "forbidden",
    ]
    assert "X-API-Key" in answers[0].json()["error"]["message"]
    assert await _logged(conn) == [
        (None, "MCP", "/mcp", 401, True),
        (None, "MCP", "/mcp", 401, True),
        (revoked.stored.key_id, "MCP", "/mcp", 401, True),
        (review.stored.key_id, "MCP", "/mcp", 403, True),
    ]


async def test_a_tool_call_is_logged_with_the_tool_and_its_key(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("lookup_company", {"query": "acme"})

    assert result.is_error is False
    answer = parsed(result)
    assert answer["data"]["matches"][0]["ticker"] == "ACME"
    assert answer["disclaimer"] == DISCLAIMER
    assert await _logged(conn) == [
        (await _key_id(conn, api_key), "MCP", "lookup_company", 200, True)
    ]


async def test_failed_tool_calls_answer_the_error_envelope_and_are_logged(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    async with connected(api_app, api_key) as client:
        too_long = await client.call_tool("lookup_company", {"query": "x" * 101})
        no_letters = await client.call_tool("lookup_company", {"query": "!!!"})

    for result in (too_long, no_letters):
        assert result.is_error is True
        assert parsed(result)["error"]["code"] == "invalid_parameter"
    assert (
        parsed(too_long)["error"]["message"] == "query: String should have at most 100 characters"
    )
    key_id = await _key_id(conn, api_key)
    assert await _logged(conn) == [
        (key_id, "MCP", "lookup_company", 400, True),
        (key_id, "MCP", "lookup_company", 400, True),
    ]


async def test_an_unknown_tool_is_404(api_app: FastAPI, api_key: str, conn: Conn) -> None:
    async with serving(api_app), http(api_app, api_key) as client:
        response = await client.post(
            "/mcp", json=tool_call("no_such_tool", {}), headers=POST_HEADERS
        )

    result = response.json()["result"]
    assert result["isError"] is True
    assert '"code":"not_found"' in result["content"][0]["text"]
    assert await _logged(conn) == [(await _key_id(conn, api_key), "MCP", "no_such_tool", 404, True)]


async def test_a_bug_is_500_without_its_details(
    api_app: FastAPI, api_key: str, conn: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: object, **kwargs: object) -> None:  # noqa: ASYNC124 - as lookup
        raise RuntimeError("boom")

    monkeypatch.setattr("marketdata.service.companies.lookup", broken)

    async with connected(api_app, api_key) as client:
        result = await client.call_tool("lookup_company", {"query": "acme"})

    assert result.is_error is True
    assert parsed(result)["error"]["code"] == "internal"
    assert "boom" not in text_of(result)
    assert await _logged(conn) == [
        (await _key_id(conn, api_key), "MCP", "lookup_company", 500, True)
    ]


async def test_tool_calls_count_against_the_keys_rate(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    limited = await create_key(conn, "test client", rate_per_min=1)

    async with serving(api_app):
        async with session(api_app, limited.key) as client:
            # The client lists the tools to check a call's result; listed first, it doesn't
            # ask again after the call, when the key has no request left this minute.
            await client.list_tools()
            first = await client.call_tool("lookup_company", {"query": "acme"})
        async with http(api_app, limited.key) as raw:
            refused = await raw.post(
                "/mcp", json=tool_call("lookup_company", {"query": "acme"}), headers=POST_HEADERS
            )

    assert first.is_error is False
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    key_id = limited.stored.key_id
    assert await _logged(conn) == [
        (key_id, "MCP", "lookup_company", 200, True),
        (key_id, "MCP", "/mcp", 429, True),
    ]


async def test_only_this_machines_host_is_accepted(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    async with serving(api_app), http(api_app, api_key) as client:
        response = await client.post(
            "/mcp", json=initialize(), headers=POST_HEADERS | {"Host": "evil.example"}
        )

    assert response.status_code == 421  # DNS rebinding protection


# --- lookup_company (D-109), on the hand-made dataset of tests/integration/api_seed.py.


async def _lookup(client: Client, query: str) -> Any:
    return parsed(await client.call_tool("lookup_company", {"query": query}))


async def test_lookup_finds_a_ticker_its_cik_and_its_name(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        answers = {
            query: await _lookup(client, query)
            for query in ("acme", "ACME", " 1001 ", "Acme Corp.", "acme corp")
        }

    for query, answer in answers.items():
        assert answer["data"]["matches"][0] == {
            "ticker": "ACME",
            "other_tickers": ["ACME-PA"],
            "name": "Acme Corp",
            "cik": ACME,
            "filer_type": "domestic",
            "covered": True,
            "reason": "A domestic filer in the covered universe: its 10-K and 10-Q apply.",
        }, query
        assert answer["sources"] == []
        assert answer["as_of"] is not None


async def test_lookup_says_why_data_doesnt_apply(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)

    async with connected(api_app, api_key) as client:
        fund = (await _lookup(client, "Index Fund"))["data"]["matches"]
        outside = (await _lookup(client, "outc"))["data"]["matches"]

    assert [(match["ticker"], match["covered"], match["reason"]) for match in fund] == [
        ("FNDX", False, "A fund: fundamentals don't apply.")
    ]
    assert [(match["ticker"], match["covered"], match["reason"]) for match in outside] == [
        ("OUTC", False, "Not in the covered universe.")
    ]


async def test_lookup_ranks_names_and_leaves_out_companies_without_a_ticker(
    api_app: FastAPI, api_key: str, conn: Conn
) -> None:
    await seed(conn)
    # Two share classes of a company whose name doesn't contain its ticker.
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (6006, 'Zeta Holdings', 'domestic', false)"
    )
    await conn.execute(
        "insert into securities (cik, ticker, security_type, valid_from) values "
        "(6006, 'ZZ-A', 'common', '2026-10-25'), (6006, 'ZZ-B', 'common', '2026-10-25')"
    )

    async with connected(api_app, api_key) as client:
        corp = (await _lookup(client, "corp"))["data"]["matches"]
        classes = (await _lookup(client, "zz"))["data"]["matches"]
        predecessor = (await _lookup(client, "Acme Holdings"))["data"]["matches"]

    # A word in both names: the universe company first.
    assert [match["ticker"] for match in corp] == ["ACME", "OUTC"]
    assert [(match["ticker"], match["other_tickers"]) for match in classes] == [("ZZ-A", ["ZZ-B"])]
    assert predecessor == []  # Acme Holdings has no current ticker; ACME took its history


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("   ", "query is empty: give a company name or a ticker, e.g. Apple"),
        ("ეპლი", "names are matched in the Latin alphabet"),
    ],
)
async def test_lookup_refuses_a_query_it_cant_match(
    api_app: FastAPI, api_key: str, query: str, message: str
) -> None:
    async with connected(api_app, api_key) as client:
        result = await client.call_tool("lookup_company", {"query": query})

    assert result.is_error is True
    assert parsed(result)["error"]["code"] == "invalid_parameter"
    assert message in parsed(result)["error"]["message"]
