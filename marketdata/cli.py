"""The `marketdata` admin CLI.

Command groups are registered up front so `marketdata --help` shows the whole tool; each epic
fills in its group. Commands load settings themselves, so `--help` works without a .env.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

import typer
from psycopg import AsyncConnection

from marketdata.config import Settings, SettingsError, load_settings
from marketdata.db.pool import create_pool
from marketdata.db.repositories.tasks import TASK_STATUSES, Task, TaskCount
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.sec_lock import SecProcessBusy, sec_process_lock
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
from marketdata.service.filings import FilingView, recent_filings
from marketdata.tasks import queue
from marketdata.tasks import scheduler as task_scheduler
from marketdata.tasks.handlers import build_registry
from marketdata.tasks.worker import serve

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
    ("filings", "A company's filings with plain-language event labels.", "E3"),
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


def _in_transaction[T](
    settings: Settings, work: Callable[[AsyncConnection[Any]], Awaitable[T]]
) -> T:
    """Run `work` on one connection; its transaction commits when `work` returns."""

    async def run() -> T:
        async with create_pool(str(settings.database_url), max_size=1) as pool:
            async with pool.connection() as conn:
                return await work(conn)

    return asyncio.run(run())


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

    database_url = str(settings.database_url)

    async def run() -> str:
        async with (
            sec_process_lock(database_url, "marketdata sec get"),
            sec,
            create_pool(database_url, max_size=2) as pool,
        ):
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
    except (SecError, RawStoreError, SecProcessBusy) as exc:
        raise _fail(str(exc)) from None


@app.command("worker")
def worker(
    concurrency: Annotated[
        int | None,
        typer.Option(min=1, max=32, help="Tasks run at once; default WORKER_CONCURRENCY."),
    ] = None,
) -> None:
    """Run queued tasks until stopped (SIGTERM or Ctrl-C), as the one process calling the SEC.

    On a stop it finishes running tasks for up to a minute and puts the rest back in the queue;
    a second Ctrl-C puts them back at once.
    """
    settings = _settings()
    try:
        sec = SecClient.from_settings(settings)
        store = create_raw_store(settings)
    except SettingsError as exc:
        raise _fail(str(exc), 2) from None
    concurrency = concurrency or settings.worker_concurrency
    try:
        asyncio.run(serve(settings, build_registry(), sec, store, concurrency=concurrency))
    except SecProcessBusy as exc:
        raise _fail(str(exc)) from None


@groups["backfill"].command("filings")
def backfill_filings(
    years: Annotated[
        int, typer.Option(min=1, max=30, help="How many years of filings to load.")
    ] = 5,
) -> None:
    """Queue a filing-history backfill for each universe company; the worker runs them.

    Each task watches the company, then reads the older pages of its submissions back to the
    start date. Exits with 1 when the universe is empty.
    """
    settings = _settings()
    since = task_scheduler.years_before(datetime.now(UTC).date(), years)

    async def work(conn: AsyncConnection[Any]) -> list[Task]:
        return await task_scheduler.enqueue_backfills(conn, since)

    queued = _in_transaction(settings, work)
    if not queued:
        raise _fail("No universe companies: run `marketdata ingest security-master` first.")
    start = since.isoformat()
    mine = [task for task in queued if task.status == "queued" and task.payload["since"] == start]
    typer.echo(
        f"Queued {len(mine)} filing backfills back to {since} for {len(queued)} universe "
        "companies; the worker runs them."
    )
    if len(mine) < len(queued):
        typer.echo(
            f"  {len(queued) - len(mine)} were already pending and keep their own start date."
        )
    typer.echo("Follow them with `marketdata tasks list --kind filings_backfill`.")


@app.command("scheduler")
def scheduler() -> None:
    """Enqueue periodic tasks until stopped (SIGTERM or Ctrl-C); the worker runs them.

    Every 10 minutes, a filings watch for each universe company; daily at 06:00 UTC, the
    security-master load (at once if started later in the day and it hasn't run).
    """
    settings = _settings()
    asyncio.run(task_scheduler.serve(settings))


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

    database_url = str(settings.database_url)

    async def run() -> LoadReport:
        async with (
            sec_process_lock(database_url, "marketdata ingest security-master"),
            sec,
            create_pool(database_url, max_size=2) as pool,
        ):
            fetcher = RawFetcher(sec, store, pool.connection)
            loader = SecurityMasterLoader(fetcher, pool.connection)
            today = datetime.now(UTC).date()
            return await loader.load(universe, overrides, as_of=today, force=force)

    try:
        report = asyncio.run(run())
    except SnapshotRefused as exc:
        raise _fail(str(exc), 2) from None
    except (SecError, RawStoreError, SecFileError, SecProcessBusy) as exc:
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


class TaskStatusOption(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    dead = "dead"


@groups["tasks"].command("list")
def tasks_list(
    status: Annotated[
        TaskStatusOption | None, typer.Option(help="Only tasks with this status.")
    ] = None,
    kind: Annotated[
        str | None, typer.Option(help="Only tasks of this kind, e.g. filings_watch.")
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000, help="How many tasks to list.")] = 50,
) -> None:
    """Count tasks by kind and status, then list the most recently changed ones."""
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> tuple[list[TaskCount], list[Task]]:
        counts = await queue.count(conn)
        listed = await queue.list_tasks(
            conn, status=status.value if status else None, kind=kind, limit=limit
        )
        return counts, listed

    counts, listed = _in_transaction(settings, work)
    for line in _task_lines(counts, listed):
        typer.echo(line)


def _task_lines(counts: list[TaskCount], listed: list[Task]) -> list[str]:
    totals = dict.fromkeys(TASK_STATUSES, 0)
    by_kind: dict[str, dict[str, int]] = {}
    for row in counts:
        totals[row.status] += row.count
        by_kind.setdefault(row.kind, {})[row.status] = row.count
    summary = ", ".join(f"{status} {total:,}" for status, total in totals.items())
    lines = [f"Tasks: {sum(totals.values()):,} ({summary})"]
    for kind, found in sorted(by_kind.items()):
        parts = [f"{status} {found[status]:,}" for status in TASK_STATUSES if status in found]
        lines.append(f"  {kind:<20} {', '.join(parts)}")
    if not listed:
        lines.append("No tasks match.")
        return lines
    lines.append(f"Most recently changed ({len(listed)}):")
    lines.append(
        f"  {'ID':>8}  {'KIND':<20} {'KEY':<22} {'STATUS':<8} {'TRIES':>5}  "
        f"{'CHANGED (UTC)':<19}  NEXT RUN (UTC)"
    )
    for task in listed:
        waiting = task.status in ("queued", "failed")
        next_run = _utc(task.run_after) if waiting else "—"
        lines.append(
            f"  {task.task_id:>8}  {task.kind:<20} {task.key:<22} {task.status:<8} "
            f"{task.attempts:>5}  {_utc(task.updated_at):<19}  {next_run}"
        )
        if task.last_error and task.status in ("failed", "dead"):
            error = " ".join(task.last_error.split())
            lines.append(f"  {'':>8}  {error[:160]}{'…' if len(error) > 160 else ''}")
    return lines


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


@groups["tasks"].command("retry")
def tasks_retry(
    task_ids: Annotated[
        list[int] | None, typer.Argument(help="IDs of dead or failed tasks to queue again.")
    ] = None,
    dead: Annotated[bool, typer.Option("--dead", help="Queue every dead task again.")] = False,
    kind: Annotated[str | None, typer.Option(help="With --dead: only tasks of this kind.")] = None,
) -> None:
    """Queue dead or failed tasks again now, with a fresh attempt count.

    Done tasks are not retried here: the command that created them runs them again (for example
    `marketdata backfill filings`).
    """
    ids = task_ids or []
    if bool(ids) == dead or (kind is not None and not dead):
        raise _fail("Give task IDs, or --dead (optionally with --kind).", 2)
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> tuple[list[Task], list[Task]]:
        if dead:
            return await queue.retry_dead(conn, kind=kind), []
        retried = await queue.retry(conn, ids)
        done = {task.task_id for task in retried}
        return retried, await queue.get_many(conn, [i for i in ids if i not in done])

    retried, skipped = _in_transaction(settings, work)
    if retried:
        typer.echo(f"Queued again ({len(retried)}):")
        for task in retried:
            typer.echo(f"  {task.task_id} {task.kind} {task.key}")
    else:
        typer.echo("Nothing to retry.")
    found = {task.task_id for task in skipped}
    for task in skipped:
        typer.echo(
            f"  Not retried: {task.task_id} is {task.status}; only dead or failed tasks are."
        )
    missing = [i for i in ids if i not in found and i not in {t.task_id for t in retried}]
    for task_id in missing:
        typer.echo(f"  Not retried: no task {task_id}.")
    if skipped or missing:
        raise typer.Exit(1)


@groups["tasks"].command("purge-done")
def tasks_purge_done(
    older_than_days: Annotated[
        int, typer.Option(min=0, help="Keep done tasks that changed within this many days.")
    ] = 7,
) -> None:
    """Delete done tasks that haven't changed for N days.

    Queued, running, failed and dead tasks are never deleted. Periodic tasks (a company's
    watch, the daily security-master load) are reused, so they stay while they run.
    """
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> int:
        return await queue.purge_done(conn, timedelta(days=older_than_days))

    deleted = _in_transaction(settings, work)
    typer.echo(f"Deleted {deleted:,} done tasks unchanged for {older_than_days} days.")


@groups["filings"].command("list")
def filings_list(
    ticker: Annotated[str, typer.Argument(help="A ticker as people write it: AAPL, brk.b, BRK/B.")],
    limit: Annotated[int, typer.Option(min=1, max=100, help="How many filings to list.")] = 20,
    form: Annotated[
        str | None, typer.Option(help="Only this form, e.g. 8-K or 'SCHEDULE 13D'.")
    ] = None,
) -> None:
    """A company's most recent filings with plain-language event labels.

    Reads the database only; the watcher and `marketdata backfill filings` fill it.
    """
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> tuple[ResolvedSecurity, list[FilingView]]:
        security = await resolve_ticker(conn, ticker)
        return security, await recent_filings(conn, security.cik, form=form, limit=limit)

    try:
        security, listed = _in_transaction(settings, work)
    except InvalidParameter as exc:
        raise _fail(str(exc), 2) from None
    except NotFound as exc:
        raise _fail(str(exc)) from None
    for line in _filing_lines(security, listed, form):
        typer.echo(line)


def _filing_lines(
    security: ResolvedSecurity, listed: list[FilingView], form: str | None
) -> list[str]:
    name = security.name or "(no name)"
    what = f"{form} filings" if form else "filings"
    if not listed:
        return [
            f"{name} ({security.ticker}, CIK {security.cik}): no {what} stored yet. The watcher "
            "and `marketdata backfill filings` load them."
        ]
    lines = [f"{name} ({security.ticker}, CIK {security.cik}): the {len(listed)} latest {what}"]
    for filing in listed:
        events = "; ".join(
            event.label + (" [high]" if event.importance == "high" else "")
            for event in filing.events
        )
        lines.append(
            f"  {filing.filed_at}  {filing.form:<16} {filing.accession_no}  {events}".rstrip()
        )
    lines.append("Information from public filings. Not investment advice.")
    return lines
