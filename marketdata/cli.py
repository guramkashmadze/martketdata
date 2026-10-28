"""The `marketdata` admin CLI.

Command groups are registered up front so `marketdata --help` shows the whole tool; each epic
fills in its group. Commands load settings themselves, so `--help` works without a .env.
"""

import asyncio
import csv
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
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
    read_predecessors,
)
from marketdata.log import configure_logging
from marketdata.normalize.public_float import Suggestion
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
from marketdata.service.earnings import FactView, ReleaseView, latest_release
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.extraction import ExtractionReport, SlotCounts, extraction_report
from marketdata.service.filings import FilingView, recent_filings
from marketdata.service.financials import MetricView, metric_series
from marketdata.service.universe import suggest
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
    ("earnings", "A company's latest earnings release, each value with its proof.", "E7a"),
    ("metrics", "A company's reported financial metrics, each with its filing.", "E4a"),
    ("stats", "Freshness and track-record statistics.", "E7c"),
    ("normalize", "Rebuild metrics from stored XBRL facts.", "E4b"),
    ("quality", "Quality checks and the accuracy report.", "E4b"),
    ("clients", "Create and list API clients.", "E5a"),
    ("keys", "Create, list and revoke API keys.", "E5a"),
    ("webhooks", "Manage webhook destinations and send test events.", "E5b"),
    ("usage", "API and MCP usage per client.", "E11"),
]

CANDIDATES_FILE = Path("var/universe_candidates.csv")  # var/ is not committed

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


@groups["backfill"].command("earnings")
def backfill_earnings(
    quarters: Annotated[
        int,
        typer.Option(min=1, max=40, help="How many quarters back, three months each."),
    ] = 4,
    force: Annotated[
        bool,
        typer.Option(help="Extract releases that were extracted before again (new rules)."),
    ] = False,
    tickers: Annotated[
        list[str] | None,
        typer.Option("--ticker", help="Only this universe company (repeatable); default: all."),
    ] = None,
) -> None:
    """Queue an earnings backfill for each domestic universe company; the worker runs them.

    Each task loads the filings of the company's predecessors, then queues the extraction of
    every 8-K with item 2.02 filed in the window, behind live work. Exits with 1 when the
    universe has no domestic company.
    """
    settings = _settings()
    since = task_scheduler.months_before(datetime.now(UTC).date(), quarters * 3)

    async def work(conn: AsyncConnection[Any]) -> list[Task]:
        ciks = await _universe_ciks(conn, tickers) if tickers else None
        return await task_scheduler.enqueue_earnings_backfills(conn, since, force=force, ciks=ciks)

    queued = _queue(settings, work)
    if not queued:
        raise _fail("No domestic universe companies: run `marketdata ingest security-master`.")
    start = since.isoformat()
    mine = [task for task in queued if task.status == "queued" and task.payload["since"] == start]
    typer.echo(
        f"Queued {len(mine)} earnings backfills back to {since} for {len(queued)} domestic "
        "universe companies; the worker runs them."
    )
    if len(mine) < len(queued):
        typer.echo(
            f"  {len(queued) - len(mine)} were already pending and keep their own start date."
        )
    typer.echo(
        "Follow them with `marketdata tasks list --kind earnings_backfill` and "
        "`--kind earnings_extract`."
    )


@groups["backfill"].command("facts")
def backfill_facts(
    tickers: Annotated[
        list[str] | None,
        typer.Option("--ticker", help="Only this universe company (repeatable); default: all."),
    ] = None,
) -> None:
    """Queue an XBRL facts refresh for each domestic universe company; the worker runs them.

    Each task stores the company's companyfacts JSON, and its predecessors', in the raw store
    and loads their us-gaap and dei facts, behind live work. Exits with 1 when no domestic
    universe company matches.
    """
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> list[Task]:
        ciks = await _universe_ciks(conn, tickers) if tickers else None
        return await task_scheduler.enqueue_facts_backfills(conn, ciks=ciks)

    queued = _queue(settings, work)
    if not queued:
        raise _fail("No domestic universe companies: run `marketdata ingest security-master`.")
    waiting = [task for task in queued if task.status != "queued" or task.attempts]
    typer.echo(
        f"Queued facts refreshes for {len(queued)} domestic universe companies; the worker "
        "runs them."
    )
    if waiting:
        typer.echo(f"  {len(waiting)} were already running or waiting to retry.")
    typer.echo("Follow them with `marketdata tasks list --kind facts_refresh`.")


@groups["backfill"].command("public-float")
def backfill_public_float() -> None:
    """Queue the load of every filer's public float from the SEC's frames API (nine quarterly
    frames of dei:EntityPublicFloat); the worker runs it. `marketdata universe suggest` reads
    the result."""
    settings = _settings()
    today = datetime.now(UTC).date()

    async def work(conn: AsyncConnection[Any]) -> list[Task]:
        return [await task_scheduler.enqueue_public_float(conn, today)]

    [task] = _queue(settings, work)
    state = "queued" if task.status == "queued" else f"already {task.status}"
    typer.echo(f"Public float load for {today}: {state}; the worker runs it.")
    typer.echo("Follow it with `marketdata tasks list --kind public_float_load`.")


async def _universe_ciks(conn: AsyncConnection[Any], tickers: list[str]) -> list[int]:
    """The CIKs of these tickers, each of a domestic universe company (InvalidParameter if
    not)."""
    ciks = []
    for text in tickers:
        security = await resolve_ticker(conn, text)
        if not security.in_universe or security.filer_type != "domestic":
            where = "in the universe" if security.in_universe else "outside the universe"
            raise InvalidParameter(
                f"{security.ticker} is a {security.filer_type} company {where}: facts and "
                "earnings are loaded for domestic universe companies only (PLAN.md §9.1)"
            )
        ciks.append(security.cik)
    return ciks


def _queue(
    settings: Settings, work: Callable[[AsyncConnection[Any]], Awaitable[list[Task]]]
) -> list[Task]:
    """Queue tasks in one transaction; a ticker that doesn't resolve exits with its reason."""
    try:
        return _in_transaction(settings, work)
    except InvalidParameter as exc:
        raise _fail(str(exc), 2) from None
    except NotFound as exc:
        raise _fail(str(exc)) from None


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
    """Load every ticker from the SEC lists, and details and class for each universe company
    and each predecessor in PREDECESSORS_FILE.

    Prints the changes and the universe with the class of each company. Exits with 1 when a
    universe company's or predecessor's submissions could not be loaded (what was stored for
    it stays), and with 2, changing nothing, when the lists look cut short.
    """
    settings = _settings()
    universe, overrides = _data_files(settings)
    try:
        links = read_predecessors(settings.predecessors_file)
    except DataFileError as exc:
        raise _fail(str(exc), 2) from None
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
            return await loader.load(universe, overrides, as_of=today, links=links, force=force)

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


@groups["universe"].command("suggest")
def universe_suggest(
    top: Annotated[int, typer.Option(min=1, max=5000, help="How many candidates to rank.")] = 500,
    out: Annotated[
        Path, typer.Option(help="The candidate CSV; the unranked list goes next to it.")
    ] = CANDIDATES_FILE,
) -> None:
    """Rank listed filers by their latest public float (dei:EntityPublicFloat) for review, and
    list apart the universe companies without a value, stale values and suspect ones (a float
    implying more than 10,000 dollars a share on the cover page's share count) (D-035, D-085).
    Reads the database only: run `marketdata backfill public-float` and the worker first."""
    settings = _settings()
    today = datetime.now(UTC).date()

    async def work(conn: AsyncConnection[Any]) -> Suggestion | None:
        return await suggest(conn, today=today, top=top)

    suggestion = _in_transaction(settings, work)
    if suggestion is None:
        raise _fail(
            "No public float loaded: run `marketdata backfill public-float` and the worker."
        )
    unranked = out.with_name(f"{out.stem}_unranked{out.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rank",
                "tickers",
                "cik",
                "name",
                "public_float_usd",
                "measured_on",
                "accession_no",
                "in_universe",
                "filer_class",
                "note",
            ]
        )
        for candidate in suggestion.candidates:
            writer.writerow(
                [
                    candidate.rank,
                    " ".join(candidate.tickers),
                    candidate.cik,
                    candidate.name or "",
                    candidate.public_float,
                    candidate.measured_on,
                    candidate.accession_no,
                    "yes" if candidate.in_universe else "no",
                    candidate.filer_type,
                    candidate.note,
                ]
            )
    with unranked.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "status",
                "tickers",
                "cik",
                "name",
                "public_float_usd",
                "measured_on",
                "accession_no",
                "note",
            ]
        )
        for row in suggestion.unranked:
            writer.writerow(
                [
                    row.status,
                    " ".join(row.tickers),
                    row.cik,
                    row.name or "",
                    "" if row.public_float is None else row.public_float,
                    row.measured_on or "",
                    row.accession_no or "",
                    row.note,
                ]
            )
    new = [candidate for candidate in suggestion.candidates if not candidate.in_universe]
    typer.echo(
        f"Ranked {len(suggestion.candidates)} listed filers by public float: {len(new)} are not in "
        f"the universe ({out})."
    )
    status = Counter(row.status for row in suggestion.unranked)
    typer.echo(
        f"Not ranked: {status['missing']} universe companies without a value, "
        f"{status['stale']} stale and {status['suspect']} suspect values ({unranked}); "
        f"{suggestion.left_out} filers left out (no listed common ticker, or a fund or foreign "
        "issuer)."
    )
    typer.echo("Review the candidates before adding any to the universe file.")


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


@groups["extract"].command("report")
def extract_report() -> None:
    """The extraction hit-rate report (PLAN.md §9.9): per slot and per company, how many slots
    passed, failed or were not found, and how many needed an override. It decides OD-10.

    Reads the stored releases as their last run left them; `marketdata backfill earnings
    --quarters 4 --force` runs them all again.
    """
    settings = _settings()
    report = _in_transaction(settings, extraction_report)
    for line in _report_lines(report):
        typer.echo(line)


def _report_lines(report: ExtractionReport) -> list[str]:
    if not report.releases:
        return ["No earnings releases are stored. `marketdata backfill earnings` loads them."]
    statuses = ", ".join(
        f"{status.replace('_', ' ')} {count:,}" for status, count in report.statuses.items()
    )
    total = report.total
    with_statement = report.releases - report.statuses["no_statement"]
    lines = [
        f"Earnings releases: {report.releases:,} ({statuses}); "
        f"rules version {', '.join(report.rules_versions)}",
        f"Hit rate: {total.passed:,} of the {total.applicable:,} slots that apply passed "
        f"({_rate(total)}) in the {with_statement:,} releases with a statement",
        "",
        f"  {'SLOT':<18}{_COUNT_HEADINGS}",
    ]
    for slot, counts in report.slots.items():
        lines.append(f"  {slot:<18}{_count_cells(counts)}")
    lines.append(f"  {'all slots':<18}{_count_cells(total)}")
    if report.failures:
        failed = ", ".join(f"{flag} {count:,}" for flag, count in report.failures.items())
        lines += ["", f"Failed slots by flag: {failed}"]
    lines += ["", f"Overrides used ({len(report.overrides)}), with the releases each was used on:"]
    for description, count in report.overrides.items():
        lines.append(f"  {count:>4}  {_cut(description, 110)}")
    if not report.overrides:
        lines.append("  none")
    lines += ["", f"  {'COMPANY':<41}{'RELEASES':>9}{_COUNT_HEADINGS}{'NO STATEMENT':>14}"]
    for company in report.companies:
        label = "predecessor" if company.successor_cik else _cut(company.name or "—", 22)
        name = f"{company.ticker or '—':<7} {company.cik:<8} {label}"
        lines.append(
            f"  {name:<41}{company.releases:>9}{_count_cells(company.slots)}"
            f"{company.no_statement:>14}"
        )
    if report.no_statement:
        lines += [
            "",
            f"No statement in {len(report.no_statement)} filings with item 2.02 (check by hand "
            "that none holds results):",
        ]
    for filing in report.no_statement:
        accepted = _utc(filing.accepted_at)[:10] if filing.accepted_at else "—"
        lines.append(f"  {filing.ticker or '—':<7} {filing.accession_no}  {accepted}  {filing.url}")
        if filing.notes:
            lines.append(f"  {'':<7} {_cut('; '.join(filing.notes), 140)}")
    return lines


_COUNT_HEADINGS = (
    f"{'PASSED':>8}{'FAILED':>8}{'NOT FOUND':>11}{'N/A':>6}{'OVERRIDES':>11}{'HIT RATE':>10}"
)


def _count_cells(counts: SlotCounts) -> str:
    return (
        f"{counts.passed:>8,}{counts.failed:>8,}{counts.not_found:>11,}"
        f"{counts.not_applicable:>6,}{counts.overridden:>11,}{_rate(counts):>10}"
    )


def _rate(counts: SlotCounts) -> str:
    return f"{counts.hit_rate}%" if counts.hit_rate is not None else "—"


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


@groups["earnings"].command("show")
def earnings_show(
    ticker: Annotated[str, typer.Argument(help="A ticker as people write it: AAPL, brk.b, BRK/B.")],
) -> None:
    """A company's latest earnings release: each value with the row it was read from, its scale
    and the checks it passed, or why it has none. Reads the database only."""
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> tuple[ResolvedSecurity, ReleaseView]:
        security = await resolve_ticker(conn, ticker)
        return security, await latest_release(conn, security.cik)

    try:
        security, release = _in_transaction(settings, work)
    except InvalidParameter as exc:
        raise _fail(str(exc), 2) from None
    except NotFound as exc:
        raise _fail(str(exc)) from None
    for line in _release_lines(security, release):
        typer.echo(line)


def _release_lines(security: ResolvedSecurity, release: ReleaseView) -> list[str]:
    filer = f", filed by its predecessor CIK {release.cik}" if release.cik != security.cik else ""
    accepted = f", accepted {_utc(release.accepted_at)} UTC" if release.accepted_at else ""
    quarter = (
        f"{release.fiscal_period} fiscal {release.fiscal_year}, ended {release.period_end}"
        if release.period_end
        else "the quarter's end date is not stated in the release"
    )
    verified = f", verified {_utc(release.verified_at)} UTC" if release.verified_at else ""
    lines = [
        f"{security.name or '(no name)'} ({security.ticker}, CIK {security.cik})",
        f"  {'Release':<10} {release.form or '8-K'} {release.accession_no}{accepted}{filer}",
        f"  {'Quarter':<10} {quarter}"
        + (f" (columns: {release.period_label})" if release.period_label else ""),
        f"  {'Status':<10} {release.status}: {release.extractor} version "
        f"{release.extractor_version}{verified}",
        f"  {'Filing':<10} {release.filing_url}",
        f"  {'Exhibit':<10} {release.document_url or '— (no statement was found)'}",
    ]
    for fact in release.facts:
        lines += _fact_lines(fact)
    lines.append("Information from public filings. Not investment advice.")
    return lines


def _fact_lines(fact: FactView) -> list[str]:
    if fact.check_status != "passed" or fact.value is None:
        status = fact.check_status.replace("_", " ")
        lines = [f"{fact.slot}: {status}"]
        lines += [f"  {'why':<10} {reason}" for reason in fact.reasons]
        if fact.quality_flags:
            lines.append(f"  {'flags':<10} {', '.join(fact.quality_flags)}")
        if fact.source_quote:
            lines.append(f"  {'row':<10} {fact.source_quote}")
        return lines
    unit = fact.unit if fact.unit == "USD" else "USD per share"
    prior = (
        f", prior-year quarter {fact.prior_value:,} {unit}" if fact.prior_value is not None else ""
    )
    moved = f", change {fact.change_pct:+}%" if fact.change_pct is not None else ""
    lines = [f"{fact.slot}: {fact.value:,} {unit}{prior}{moved}"]
    if fact.quality_flags:
        lines.append(f"  {'flags':<10} {', '.join(fact.quality_flags)}")
    lines += [
        f"  {'row':<10} {fact.source_quote}",
        f"  {'scale':<10} {fact.scale_text or '— (per-share values are not scaled)'}",
        f"  {'checks':<10} {', '.join(fact.checks)} passed; {fact.method} "
        f"version {fact.extractor_version}",
    ]
    return lines


class PeriodOption(StrEnum):
    Q = "Q"
    FY = "FY"
    TTM = "TTM"


@groups["metrics"].command("show")
def metrics_show(
    ticker: Annotated[str, typer.Argument(help="A ticker as people write it: AAPL, brk.b, BRK/B.")],
    metric: Annotated[
        str, typer.Option(help="A catalog or computed metric, e.g. revenue or net_margin.")
    ] = "revenue",
    period: Annotated[
        PeriodOption,
        typer.Option(help="Q (quarters), FY (fiscal years) or TTM (trailing twelve months)."),
    ] = PeriodOption.Q,
    limit: Annotated[int, typer.Option(min=1, max=40, help="How many periods.")] = 8,
) -> None:
    """A company's values of one metric, newest first, each with how it was made (reported,
    derived or computed), its XBRL concept and the filing it came from. Reads the database
    only."""
    settings = _settings()

    async def work(conn: AsyncConnection[Any]) -> tuple[ResolvedSecurity, list[MetricView]]:
        security = await resolve_ticker(conn, ticker)
        rows = await metric_series(conn, security.cik, metric, period.value, limit=limit)
        return security, rows

    try:
        security, rows = _in_transaction(settings, work)
    except InvalidParameter as exc:
        raise _fail(str(exc), 2) from None
    except NotFound as exc:
        raise _fail(str(exc)) from None
    for line in _metric_lines(security, metric, period.value, rows):
        typer.echo(line)


def _metric_lines(
    security: ResolvedSecurity, metric: str, period: str, rows: list[MetricView]
) -> list[str]:
    kind = {"Q": "quarters", "FY": "fiscal years", "TTM": "trailing twelve months"}[period]
    lines = [
        f"{security.name or '(no name)'} ({security.ticker}, CIK {security.cik}): {metric}, {kind}"
    ]
    if not rows:
        lines.append("  No values: the company's filings tag none of the concepts it needs.")
    for row in rows:
        label = f"{row.fiscal_year} {row.fiscal_period}"
        days = (
            f"{row.period_start} to {row.period_end}"
            if row.period_start
            else f"at {row.period_end}"
        )
        value = "—" if row.value is None else _amount(row.value, row.unit)
        filing = (
            f"{row.proof.accession_no} filed {row.filed_at}"
            if row.proof.accession_no
            else "no filing"
        )
        filer = f" (CIK {row.cik})" if row.cik != security.cik else ""
        lines.append(f"  {label:<8} {days:<26} {value:>26}  {filing}{filer}")
        details = [row.proof.method, row.proof.concept or "—", *row.quality_flags]
        lines.append(f"  {'':<8} {', '.join(details)}")
    lines.append("Information from public filings. Not investment advice.")
    return lines


def _amount(value: Decimal, unit: str) -> str:
    return f"{value}%" if unit == "percent" else f"{value:,} {unit}"
