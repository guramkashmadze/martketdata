"""The `marketdata` admin CLI.

Command groups are registered up front so `marketdata --help` shows the whole tool; each epic
fills in its group. Commands load settings themselves, so `--help` works without a .env.
"""

import asyncio
from datetime import UTC, datetime
from typing import Annotated

import typer

from marketdata.config import Settings, SettingsError, load_settings
from marketdata.db.pool import create_pool
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.security_master import (
    LoadReport,
    SecurityMasterLoader,
    UniverseCheck,
    check_universe,
    read_data_files,
)
from marketdata.log import configure_logging
from marketdata.normalize.security_master import SecFileError, SnapshotRefused
from marketdata.normalize.universe import DataFileError, SecurityOverride, UniverseEntry
from marketdata.sec.client import SecClient, SecError
from marketdata.sec.raw_store import RawStoreError, create_raw_store
from marketdata.service.companies import (
    CompanyProfile,
    ListedSecurity,
    ResolvedSecurity,
    company_profile,
    resolve_ticker,
)
from marketdata.service.errors import InvalidParameter, NotFound

app = typer.Typer(
    help="marketdata: trusted, verified data from SEC filings. Information, not investment advice.",
    no_args_is_help=True,
)

# Group name, help text, and the epic that adds its first command (PLAN.md §12).
_GROUPS = [
    ("sec", "Fetch from the SEC through the rate-limited client.", "E1"),
    ("ingest", "Load the security master and filings into the database.", "E2"),
    ("universe", "Load and grow the covered universe.", "E2"),
    ("company", "Look up a company: identity, class, tickers and what data applies.", "E2"),
    ("tasks", "List, retry and purge queued tasks.", "E3"),
    ("backfill", "Load history: filings, earnings, facts, prices, insiders, funds.", "E3"),
    ("extract", "Earnings extraction and the hit-rate report.", "E7a"),
    ("stats", "Freshness and track-record statistics.", "E7c"),
    ("normalize", "Rebuild metrics from stored XBRL facts.", "E4b"),
    ("quality", "Quality checks and the accuracy report.", "E4b"),
    ("clients", "Create and list API clients.", "E5a"),
    ("keys", "Create, list and revoke API keys.", "E5a"),
    ("webhooks", "Manage webhook destinations and send test events.", "E5b"),
    ("usage", "API and MCP usage per client.", "E11"),
]

groups: dict[str, typer.Typer] = {}
for _name, _help, _epic in _GROUPS:
    groups[_name] = typer.Typer(no_args_is_help=True)
    app.add_typer(groups[_name], name=_name, help=f"{_help} (from {_epic})")


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _settings() -> Settings:
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise _fail(str(exc), 2) from None
    configure_logging(settings.env, settings.log_level)
    return settings


@groups["sec"].command("get")
def sec_get(
    url: Annotated[str, typer.Argument(help="An SEC URL, e.g. a submissions JSON or a document.")],
    key: Annotated[
        str | None,
        typer.Option(help="Raw key to store it under; derived from the URL when omitted."),
    ] = None,
) -> None:
    """Fetch URL through the SEC client, keep it in the raw store and print its raw key."""
    settings = _settings()
    try:
        # Fail before any request when SEC_USER_AGENT or the raw store is not configured.
        sec = SecClient.from_settings(settings)
        store = create_raw_store(settings)
    except SettingsError as exc:
        raise _fail(str(exc), 2) from None

    async def run() -> str:
        async with sec, create_pool(str(settings.database_url), max_size=2) as pool:
            fetcher = RawFetcher(sec, store, pool.connection)
            if key is None:
                stored_key, _ = await fetcher.fetch_url(url)
                return stored_key
            await fetcher.fetch_and_store(url, key)
            return key

    try:
        typer.echo(asyncio.run(run()))
    except ValueError as exc:  # no key layout for this URL, or a bad key
        raise _fail(str(exc), 2) from None
    except (SecError, RawStoreError) as exc:
        raise _fail(str(exc)) from None


def _data_files(settings: Settings) -> tuple[list[UniverseEntry], list[SecurityOverride]]:
    try:
        return read_data_files(settings.universe_file, settings.security_overrides_file)
    except DataFileError as exc:
        raise _fail(str(exc), 2) from None


@groups["ingest"].command("security-master")
def ingest_security_master(
    force: Annotated[
        bool,
        typer.Option(help="Apply lists that end more tickers than the safety limit allows."),
    ] = False,
) -> None:
    """Load every ticker from the SEC lists, and details and class for each universe company.

    Prints the changes and the universe with the class of each company. Exits with 1 when a
    universe company's submissions could not be loaded (what was stored for it stays), and
    with 2, changing nothing, when the lists look cut short.
    """
    settings = _settings()
    universe, overrides = _data_files(settings)
    try:
        sec = SecClient.from_settings(settings)
        store = create_raw_store(settings)
    except SettingsError as exc:
        raise _fail(str(exc), 2) from None

    async def run() -> LoadReport:
        async with sec, create_pool(str(settings.database_url), max_size=2) as pool:
            fetcher = RawFetcher(sec, store, pool.connection)
            loader = SecurityMasterLoader(fetcher, pool.connection)
            today = datetime.now(UTC).date()
            return await loader.load(universe, overrides, as_of=today, force=force)

    try:
        report = asyncio.run(run())
    except SnapshotRefused as exc:
        raise _fail(str(exc), 2) from None
    except (SecError, RawStoreError, SecFileError) as exc:
        raise _fail(str(exc)) from None
    for line in report.lines(str(settings.universe_file)):
        typer.echo(line)
    if report.failures:
        raise typer.Exit(1)


@groups["universe"].command("check")
def universe_check() -> None:
    """Check the universe file against the stored security master, without calling the SEC.

    Exits with 1 when a ticker is in no SEC list as of the last load.
    """
    settings = _settings()
    universe, _ = _data_files(settings)

    async def run() -> UniverseCheck:
        async with create_pool(str(settings.database_url), max_size=1) as pool:
            async with pool.connection() as conn:
                return await check_universe(conn, universe)

    check = asyncio.run(run())
    for line in check.lines(str(settings.universe_file)):
        typer.echo(line)
    if check.universe.unknown:
        raise typer.Exit(1)


@groups["company"].command("show")
def company_show(
    ticker: Annotated[str, typer.Argument(help="A ticker as people write it: AAPL, brk.b, BRK/B.")],
) -> None:
    """Show the company behind a ticker: CIK, name, class, SIC, fiscal year end, its tickers and
    which data applies. Reads the stored security master; calls nothing outside."""
    settings = _settings()

    async def run() -> tuple[ResolvedSecurity, CompanyProfile]:
        async with create_pool(str(settings.database_url), max_size=1) as pool:
            async with pool.connection() as conn:
                security = await resolve_ticker(conn, ticker)
                return security, await company_profile(conn, security.cik)

    try:
        security, profile = asyncio.run(run())
    except InvalidParameter as exc:
        raise _fail(str(exc), 2) from None
    except NotFound as exc:
        raise _fail(str(exc)) from None
    for line in _profile_lines(security, profile):
        typer.echo(line)


def _profile_lines(security: ResolvedSecurity, profile: CompanyProfile) -> list[str]:
    def describe(listed: ListedSecurity) -> str:
        parts: list[str] = [listed.security_type]
        if listed.share_class:
            parts.append(f"class {listed.share_class}")
        if listed.exchange:
            parts.append(listed.exchange)
        return f"{listed.ticker} ({', '.join(parts)})"

    own = next(listed for listed in profile.securities if listed.ticker == security.ticker)
    others = [describe(listed) for listed in profile.securities if listed is not own]
    shown = ", ".join(others[:10]) + (f" and {len(others) - 10} more" if len(others) > 10 else "")
    universe = "in the covered universe" if profile.in_universe else "not in the covered universe"
    sic = " ".join(part for part in (profile.sic_code, profile.sic_description) if part)
    lines = [
        profile.name or "(no name: the company is listed only in the SEC fund list)",
        f"  {'CIK':<16} {profile.cik}",
        f"  {'Class':<16} {profile.filer_type}, {universe}",
        f"  {'SIC':<16} {sic or '—'}",
        f"  {'Fiscal year end':<16} {profile.fiscal_year_end or '—'} (MMDD, as the SEC gives it)",
        f"  {'Incorporated':<16} {profile.state_of_inc or '—'}",
        f"  {'Ticker':<16} {describe(own)}, first seen {own.listed_since}",
        f"  {'Other tickers':<16} {shown or 'none'}",
    ]
    for capability in profile.capabilities.kinds:
        verdict = "applies" if capability.applies else "doesn't apply"
        lines.append(f"  {capability.kind.capitalize():<16} {verdict}: {capability.reason}")
    lines.append("Information from public filings. Not investment advice.")
    return lines
