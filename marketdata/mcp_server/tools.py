"""MCP tools from PLAN.md §10.3 (E6).

Each tool answers from the same service functions as the API, written as JSON text by
service/json_text.py, so numbers keep every digit (D-096). The answers are compact
(mcp_server/answers.py, D-108): lists stop at 20 filings, get_financials answers at most 16
metrics and 40 values and compare_companies 25, and without metrics both use five headline
ones. Descriptions say when to use the tool and what units it returns. Tools only read (D-014:
the server serves data to AI clients, it never calls a model).

With PUBLIC_BASE_URL set, an answer whose values have proof pages (E15) carries `proof_url`:
the release's page, or the address of each value's page with {placeholders} to fill in from
the value, so a long answer names it once and stays under 8 KB. 13F positions name a security by
its ticker, without the CUSIP their pages need, so their answers link the 13F filing instead.
"""

import inspect
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from psycopg import AsyncConnection
from pydantic import Field

from marketdata.mcp_server import answers
from marketdata.normalize.metric_catalog import UNITS
from marketdata.service import (
    companies,
    compare,
    earnings,
    filings,
    financials,
    funds,
    insiders,
    snapshot,
)
from marketdata.service.errors import InvalidParameter
from marketdata.service.json_text import dumps
from marketdata.service.served import DISCLAIMER, Served, checked_fields

# A pool's `connection` method, as api/deps.py's Connect: a connection that commits on exit.
type Connect = Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

# Without metrics, get_financials and compare_companies answer these (D-108).
HEADLINE: tuple[str, ...] = (
    "revenue",
    "operating_income",
    "net_income",
    "eps_diluted",
    "free_cash_flow",
)
MAX_VALUES = 40  # metric values get_financials answers: metrics x periods
MAX_METRICS = 16  # metrics get_financials answers: as many as the core set
MAX_COMPARED = 25  # values compare_companies answers: companies x metrics
MAX_PERIODS = 12
FILINGS_LIMIT = 20
MAX_DAYS = 365
MAX_TICKER = 20
MAX_FORM = 20
INSIDER_TRADES = 12  # trades insider_activity answers, newest first (E8)
INSIDER_LINES = 8  # insiders it summarizes, by latest trade
MAX_CODES = 20
PORTFOLIO_LARGEST = 20  # positions investor_portfolio lists by value (E9, §10.3)
PORTFOLIO_CHANGES = 6  # of each change type: new, added, trimmed, exited
FUND_HOLDERS = 20  # managers fund_holders lists, by name
MAX_MANAGER = 80
MAX_QUARTER = 10
UNIT_WORDS = {"USD": "in USD", "shares": "in shares", "USD/shares": "in USD per share"}


def _metric_names() -> str:
    by_unit: dict[str, list[str]] = {}
    for metric, unit in UNITS.items():
        by_unit.setdefault(unit, []).append(metric)
    return "; ".join(
        f"{UNIT_WORDS.get(unit, 'in ' + unit)}: {', '.join(names)}"
        for unit, names in by_unit.items()
    )


METRICS_HELP = (
    f"Metric names, by default {', '.join(HEADLINE)}. The catalog: {_metric_names()}. Margins "
    "and growth are percents; growth compares with the same period a year earlier."
)
PERIOD_HELP = "Q for quarters, FY for fiscal years, TTM for trailing twelve months."

# Plain aliases, not `type` statements: their schemas are written inline, not as $ref.
Ticker = Annotated[
    str,
    Field(
        description="The company's ticker, e.g. AAPL or BRK.B; lookup_company finds it from a "
        "name.",
        max_length=MAX_TICKER,
    ),
]
Metrics = Annotated[list[str] | None, Field(description=METRICS_HELP)]


def today() -> date:
    """Today in UTC: the end of recent_filings' window, and the day stale_data is decided."""
    return datetime.now(UTC).date()


def answer(result: answers.Answer[Any] | Served[Any], proof_url: str | None = None) -> str:
    """An answer, or the API's envelope, with its proof pages' address when there is one and
    the disclaimer, as JSON text."""
    written = checked_fields(result.model_dump(mode="python"))
    if proof_url is not None:
        written["proof_url"] = proof_url
    return dumps({**written, "disclaimer": DISCLAIMER})


def check_count(count: int, maximum: int, asked: str) -> None:
    if count > maximum:
        raise InvalidParameter(
            f"{asked}; one call answers at most {maximum}. Ask for fewer, or split the "
            "question into several calls."
        )


def wanted(metrics: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(metrics)) if metrics else HEADLINE


class Tools:
    """The tools, each reading through a connection of the API's own (`connect`, called when a
    tool runs, gives the app's current connection factory)."""

    def __init__(self, connect: Callable[[], Connect], base_url: str | None = None) -> None:
        self._connect = connect
        self._base_url = base_url  # PUBLIC_BASE_URL: the proof pages' site

    def _proof(self, path: str) -> str | None:
        """A proof page's address; none when the server doesn't know its public address."""
        return f"{self._base_url}{path}" if self._base_url else None

    def register(self, server: MCPServer[Any]) -> None:
        for tool, title in (
            (self.lookup_company, "Look up a company"),
            (self.company_snapshot, "Company snapshot"),
            (self.get_financials, "Financial metrics"),
            (self.recent_filings, "Recent filings"),
            (self.compare_companies, "Compare companies"),
            (self.latest_earnings, "Latest earnings release"),
            (self.insider_activity, "Insider trades"),
            (self.investor_portfolio, "Investor portfolio"),
            (self.fund_holders, "Funds holding a company"),
        ):
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
                max_length=companies.MAX_QUERY,
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
            served = await companies.lookup(conn, query)
        return answer(answers.lookup(served))

    async def company_snapshot(self, ticker: Ticker) -> str:
        """A one-call overview of a covered company: who it is (name, CIK, tickers, exchange,
        SIC industry, fiscal year end), its latest reported quarter, the trailing-twelve-month
        value of each core metric, its latest earnings release and its three latest filings
        of results and events. Use it for "tell me about <company>" questions; use
        get_financials for history and latest_earnings for a release's full proof.

        Amounts are in base units: USD, shares, or USD per share for EPS; margins and growth
        are percents. Each value names its period end, the filing it came from (its accession
        number; `sources` gives the SEC link), the method and the XBRL concept. A null value
        comes with a flag or a reason; funds and foreign issuers are answered with why their
        data doesn't apply.

        With the server's public address set, `proof_url` is each value's proof page (the
        filing, the checks, the history): fill in its {placeholders} from the value.

        `checked_at` is when the company's filings and financials were last checked against the
        SEC; with `check_overdue` true (more than a day ago, or never) newer filings may be
        missing.

        The data is information from public filings, not investment advice.
        """
        async with self._connect()() as conn:
            served = await snapshot.company_snapshot(conn, ticker, today=today())
        listed = quote(served.data.company.ticker, safe="")
        return answer(
            answers.snapshot(served),
            self._proof(f"/proof/metrics/{listed}/{{metric}}/TTM/{{period_end}}"),
        )

    async def get_financials(
        self,
        ticker: Ticker,
        metrics: Metrics = None,
        period: Annotated[Literal["Q", "FY", "TTM"], Field(description=PERIOD_HELP)] = "Q",
        limit: Annotated[
            int,
            Field(
                description="How many periods of each metric, newest first (1 to 12).",
                ge=1,
                le=MAX_PERIODS,
            ),
        ] = 8,
    ) -> str:
        """A company's financial metrics from its 10-Q and 10-K filings, newest period first:
        quarters, fiscal years or trailing twelve months. Use it for history and trends, e.g.
        "how did revenue and free cash flow change over the last 8 quarters".

        Each value comes with its period end, fiscal year and period, the filing it came from
        (its accession number; `sources` gives the SEC link), how it was found (reported in
        the filing, derived from year-to-date values, or computed from other metrics) and the
        XBRL concept. Amounts are in base units: USD, shares, or USD per share; margins and
        growth are percents. A null value comes with a flag that says why. One call answers at
        most 16 metrics and 40 values (metrics x periods).

        With the server's public address set, `proof_url` is each value's proof page (the
        filing, the checks, the history): fill in its {placeholders} from the value.

        `checked_at` is when the company's financials were last checked against its SEC filings;
        with `check_overdue` true (more than a day ago, or never) a newer 10-Q or 10-K may be
        missing.

        The data is information from public filings, not investment advice.
        """
        names = wanted(metrics)
        check_count(len(names), MAX_METRICS, f"{len(names)} metrics are asked for")
        count = len(names) * limit
        check_count(count, MAX_VALUES, f"{len(names)} metrics x {limit} periods is {count} values")
        async with self._connect()() as conn:
            served = await financials.get_financials(
                conn, ticker, metrics=names, period=period, limit=limit, today=today()
            )
        listed = quote(served.data.ticker, safe="")
        return answer(
            answers.financials(served),
            self._proof(f"/proof/metrics/{listed}/{{metric}}/{period}/{{period_end}}"),
        )

    async def recent_filings(
        self,
        ticker: Ticker,
        form: Annotated[
            str | None,
            Field(
                description="One form, e.g. 8-K, 10-Q, 10-K, 4 or 'SCHEDULE 13D'. Without it: "
                "annual, quarterly and current reports (10-K, 10-Q, 8-K, 20-F, 40-F, 6-K and "
                "their amendments) and 5% stakes in the company (13D, 13G).",
                max_length=MAX_FORM,
            ),
        ] = None,
        days: Annotated[
            int,
            Field(description="Filings of the last this many days (1 to 365).", ge=1, le=MAX_DAYS),
        ] = 90,
    ) -> str:
        """A company's recent SEC filings, newest first, at most 20, each with its form,
        filing date, period, SEC link and plain-language event labels (e.g. "Quarterly or
        annual results", "Director or executive change", "An investor crossed 5% and may seek
        to influence the company") with their importance. Use it for "what has <company> filed
        or announced lately" questions. `more` says that more filings match than are shown:
        ask for fewer days or one form. Works for every filer, funds and foreign issuers too.

        `checked_at` is when the company's filings were last checked on the SEC; with
        `check_overdue` true (more than a day ago, or never) newer filings may be missing.

        The data is information from public filings, not investment advice.
        """
        since = today() - timedelta(days=days)
        async with self._connect()() as conn:
            served = await filings.recent_filings(
                conn,
                ticker,
                form=form,
                reports_only=not form,
                since=since,
                limit=FILINGS_LIMIT,
            )
        return answer(answers.filings(served, form=form or None, since=since))

    async def compare_companies(
        self,
        tickers: Annotated[
            list[str],
            Field(
                description="2 to 5 tickers, e.g. ['KO', 'PG'].",
                min_length=2,
                max_length=5,
            ),
        ],
        metrics: Metrics = None,
        period: Annotated[
            Literal["Q", "FY", "TTM"],
            Field(
                description=PERIOD_HELP + " TTM by default, which compares fiscal years "
                "that end in different months."
            ),
        ] = "TTM",
    ) -> str:
        """Two to five companies side by side: for each metric, each company's newest value
        of the period type, with its own period end. Values are shown side by side, never
        ranked or scored.

        Each value names its period end, the filing it came from (its accession number;
        `sources` gives the SEC link), the method and the XBRL concept. Amounts are in base
        units: USD, shares, or USD per share; margins and growth are percents. A company none
        of whose filings reports a metric has null there. One call answers at most 25 values
        (companies x metrics).

        With the server's public address set, `proof_url` is each value's proof page (the
        filing, the checks, the history): fill in its {placeholders} from the value.

        `checked_at` is the oldest of the companies' last checks against the SEC; with
        `check_overdue` true (more than a day ago, or never) newer filings may be missing.

        The data is information from public filings, not investment advice.
        """
        names = wanted(metrics)
        count = len(tickers) * len(names)
        asked = f"{len(tickers)} companies x {len(names)} metrics is {count} values"
        check_count(count, MAX_COMPARED, asked)
        async with self._connect()() as conn:
            served = await compare.compare(
                conn, tickers, metrics=names, period=period, today=today()
            )
        return answer(
            answers.comparison(served),
            self._proof(f"/proof/metrics/{{ticker}}/{{metric}}/{period}/{{period_end}}"),
        )

    async def latest_earnings(self, ticker: Ticker) -> str:
        """A company's latest quarterly results release (the 8-K press release), as verified
        by code: revenue, operating income, net income and diluted EPS for the quarter, each
        with the same quarter a year earlier and the change in percent. Use it for "what did
        <company> report" questions.

        Each value carries its full proof: the press release's SEC link, the row exactly as
        printed (source_quote), the scale (e.g. in millions), the checks it passed, and the
        cross-check against the later 10-Q or 10-K (pending until that filing arrives). A
        value that failed a check or wasn't found is null with the reasons; `status` says
        whether the release is complete, partial or failed. Amounts are in USD, EPS in USD
        per share. With the server's public address set, `proof_url` is the release's proof
        page.

        `checked_at` is when the company's filings were last checked on the SEC; with
        `check_overdue` true (more than a day ago, or never) a newer release may be missing.

        The data is information from public filings, not investment advice.
        """
        async with self._connect()() as conn:
            served = await earnings.latest_earnings(conn, ticker)
        return answer(served, self._proof(f"/proof/earnings/{served.data.release.accession_no}"))

    async def insider_activity(
        self,
        ticker: Ticker,
        days: Annotated[
            int,
            Field(description="Trades of the last this many days (1 to 365).", ge=1, le=MAX_DAYS),
        ] = 90,
        codes: Annotated[
            list[str] | None,
            Field(
                description="Form 4 transaction codes, by default P and S: purchases and sales "
                "in the open market or privately. Others: A grant or award, M exercise or "
                "conversion of a derivative, F shares withheld for tax, G gift, D disposition "
                "to the issuer, J other; ['all'] for every code.",
                max_length=MAX_CODES,
            ),
        ] = None,
    ) -> str:
        """Who among a company's directors, officers and 10% owners bought or sold its shares,
        from their Form 4 filings with the SEC: totals per transaction code over the window,
        each insider's trades summed (at most 8, newest activity first, never ordered by size),
        and the newest trades, at most 12, each per insider, day, code and security with shares, the
        average price weighted by shares, the value and the filing it comes from. Shares are
        units of the security; prices are USD per share and values USD. `rule_10b5_1` is the
        filing's box: a trade in it was made under a pre-arranged trading plan. A 4/A replaces
        the lines it corrects, and a filing made twice counts once. Use it for "have insiders
        been buying or selling <company>" questions; `more_trades` says that more trades match
        than are listed: ask for fewer days or one code. Domestic companies, and foreign
        private issuers from March 2026. With the server's public address set, `proof_url` is
        each trade's proof page, the Form 4 as read: fill in its {accession_no}.

        `checked_at` is when the company's filings were last checked on the SEC; with
        `check_overdue` true (more than a day ago, or never) newer Forms 4 may be missing.

        The data is information from public filings, not investment advice.
        """
        async with self._connect()() as conn:
            served = await insiders.insider_activity(
                conn,
                ticker,
                days=days,
                codes=codes,
                limit=INSIDER_TRADES,
                today=today(),
                max_days=MAX_DAYS,
            )
        return answer(
            answers.insiders(served, max_insiders=INSIDER_LINES),
            self._proof("/proof/insiders/{accession_no}"),
        )

    async def investor_portfolio(
        self,
        manager: Annotated[
            str,
            Field(
                description="A curated investor by name (Berkshire, Pershing Square, Icahn, "
                "Pabrai ...) or by its CIK; an unknown name gets the list of curated ones.",
                max_length=MAX_MANAGER,
            ),
        ],
        quarter: Annotated[
            str | None,
            Field(
                description="The quarter, as 2026Q2 or its end date 2026-06-30; by default the "
                "latest one the investor has reported.",
                max_length=MAX_QUARTER,
            ),
        ] = None,
    ) -> str:
        """What a well-known investor reported holding at a quarter end in its 13F filing with
        the SEC, and what changed from its report for the quarter before: the position count and
        value, changes per type, the 20 largest positions and at most 6 of each change type:
        new, added, trimmed (shares up or down more than 1%) and exited, each with shares, value
        in USD, weight among its long equity positions (percent) and shares changed,
        split-adjusted when a split happened. Use it for "what does <investor> own" and "what
        did <investor> buy and sell last quarter". A 13F lists long positions in US-listed
        securities and options on them at the quarter end, filed up to 45 days later: say the
        quarter and the filing date. `more` names the lists that were cut short.

        `checked_at` is when the investor's filings were last checked on the SEC; with
        `check_overdue` true (more than a day ago, or never) a newer 13F may be missing.

        The data is information from public filings, not investment advice.
        """
        async with self._connect()() as conn:
            served = await funds.fund_holdings(conn, manager, quarter=quarter, limit=None)
        return answer(
            answers.portfolio(served, largest=PORTFOLIO_LARGEST, per_change=PORTFOLIO_CHANGES)
        )

    async def fund_holders(self, ticker: Ticker) -> str:
        """Which of the curated well-known investors held a company's shares in their latest
        13F filing with the SEC, listed by investor name (never ranked by size): for each, the
        quarter, the filing date, shares, value in USD, weight in its portfolio (percent) and
        the change from its quarter before; investors that sold all of it that quarter are
        listed as exited. Use it for "which famous investors own <company>". A 13F reports
        positions at a quarter end, filed up to 45 days later.

        `checked_at` is the oldest of the curated investors' last checks on the SEC; with
        `check_overdue` true (more than a day ago, or never) a newer 13F may be missing.

        The data is information from public filings, not investment advice.
        """
        async with self._connect()() as conn:
            served = await funds.company_funds(conn, ticker)
        return answer(answers.holders(served, limit=FUND_HOLDERS))
