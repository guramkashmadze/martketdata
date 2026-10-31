"""MCP tools from PLAN.md §10.3 (E6).

Each tool answers from the same service functions as the API, with the API's envelope (data,
sources, as_of, quality flags, disclaimer) written as JSON text by service/json_text.py, so
numbers keep every digit (D-096). Descriptions say when to use the tool and what units it
returns. Tools only read (D-014: the server serves data to AI clients, it never calls a model).
"""

import inspect
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from psycopg import AsyncConnection
from pydantic import Field

from marketdata.service.companies import MAX_QUERY, lookup
from marketdata.service.json_text import dumps
from marketdata.service.served import DISCLAIMER, Served

# A pool's `connection` method, as api/deps.py's Connect: a connection that commits on exit.
type Connect = Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def answer(served: Served[Any]) -> str:
    """The API's envelope as JSON text."""
    return dumps({**served.model_dump(mode="python"), "disclaimer": DISCLAIMER})


class Tools:
    """The tools, each reading through a connection of the API's own (`connect`, called when a
    tool runs, gives the app's current connection factory)."""

    def __init__(self, connect: Callable[[], Connect]) -> None:
        self._connect = connect

    def register(self, server: MCPServer[Any]) -> None:
        for tool, title in ((self.lookup_company, "Look up a company"),):
            server.add_tool(
                tool,
                name=tool.__name__,
                title=title,
                description=inspect.cleandoc(tool.__doc__ or ""),
                annotations=READ_ONLY,
                structured_output=False,
            )

    async def lookup_company(
        self,
        query: Annotated[
            str,
            Field(
                description="A company name, a ticker or a CIK, e.g. 'Apple', 'BRK.B' or "
                "'320193'.",
                max_length=MAX_QUERY,
            ),
        ],
    ) -> str:
        """Find a US-listed company's ticker from its name, a ticker or its SEC CIK. Use it
        before the other tools when you don't know the ticker, or to check whether a company
        is covered.

        Returns up to 10 matches, closest first: the ticker (the one asked for, or the
        company's primary ticker), its other tickers, the name as the SEC lists it, the CIK,
        the filer type (domestic, foreign_private_issuer, fund, other or unknown) and whether
        its earnings and financials are covered, with the reason. Names are matched in the
        Latin alphabet (Alphabet, not Google).

        The data is information from public filings, not investment advice.
        """
        async with self._connect()() as conn:
            served = await lookup(conn, query)
        return answer(served)
