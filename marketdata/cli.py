"""The `marketdata` admin CLI.

Command groups are registered up front so `marketdata --help` shows the whole tool; each epic
fills in its group. Commands load settings themselves, so `--help` works without a .env.
"""

import asyncio
from typing import Annotated

import typer

from marketdata.config import Settings, SettingsError, load_settings
from marketdata.db.pool import create_pool
from marketdata.ingest.fetch import RawFetcher
from marketdata.log import configure_logging
from marketdata.sec.client import SecClient, SecError
from marketdata.sec.raw_store import RawStoreError, create_raw_store

app = typer.Typer(
    help="marketdata: trusted, verified data from SEC filings. Information, not investment advice.",
    no_args_is_help=True,
)

# Group name, help text, and the epic that adds its first command (PLAN.md §12).
_GROUPS = [
    ("sec", "Fetch from the SEC through the rate-limited client.", "E1"),
    ("ingest", "Load the security master and filings into the database.", "E2"),
    ("universe", "Load and grow the covered universe.", "E2"),
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
