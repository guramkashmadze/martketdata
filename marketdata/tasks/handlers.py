"""The registered task kinds and their handlers (E3-S3 onward).

Handlers are thin wrappers around ingest/: they turn what an ingest step reports into success,
a retry (an exception) or TaskRefused, so ingest/ never depends on the queue.
"""

from datetime import UTC, date, datetime

import structlog

from marketdata.db.repositories.tasks import Task
from marketdata.extract.earnings_rules import OverrideError
from marketdata.ingest.earnings import EarningsBackfill, EarningsIngest, read_overrides
from marketdata.ingest.facts import FactsLoader
from marketdata.ingest.security_master import (
    SecurityMasterLoader,
    read_data_files,
    read_predecessors,
)
from marketdata.ingest.watcher import FilingsWatcher, watch_summary
from marketdata.normalize.filings import EARNINGS_EXTRACT, FACTS_REFRESH
from marketdata.normalize.security_master import SnapshotRefused
from marketdata.normalize.universe import DataFileError
from marketdata.sec import urls
from marketdata.tasks.kinds import (
    EARNINGS_BACKFILL,
    FILINGS_BACKFILL,
    FILINGS_WATCH,
    SECURITY_MASTER,
)
from marketdata.tasks.registry import Registry, TaskContext, TaskRefused

log = structlog.get_logger(__name__)


class SecurityMasterIncomplete(Exception):
    """Some universe companies' submissions could not be loaded; the rest of the load stands.
    The task is retried, and each retry loads everything again (an idempotent load)."""


async def load_security_master(context: TaskContext, task: Task) -> None:
    """The daily load of E2's security master (PLAN.md §9.2) for the day it runs.

    A broken universe file, or ticker lists that would end more tickers than the safety limit
    allows (D-054), are refused at once and change nothing: retrying the same lists can't help,
    so the task is dead and logged as an error for the operator, who checks the lists and runs
    `marketdata ingest security-master --force` if they are right.
    """
    settings = context.settings
    try:
        universe, overrides = read_data_files(
            settings.universe_file, settings.security_overrides_file
        )
        links = read_predecessors(settings.predecessors_file)
    except DataFileError as exc:
        raise TaskRefused(str(exc)) from exc
    loader = SecurityMasterLoader(context.fetcher, context.connect)
    try:
        report = await loader.load(universe, overrides, as_of=datetime.now(UTC).date(), links=links)
    except SnapshotRefused as exc:
        raise TaskRefused(str(exc)) from exc
    if report.failures:
        failed = ", ".join(f"CIK {member.cik}: {member.error}" for member in report.failures)
        raise SecurityMasterIncomplete(
            f"submissions of {len(report.failures)} universe companies were not loaded: {failed}"
        )


async def watch_filings(context: TaskContext, task: Task) -> None:
    """One company's filings watch (E3-S4), every 10 minutes from the scheduler."""
    cik = _cik(task)
    report = await FilingsWatcher(context.fetcher, context.connect, context.kinds).watch(cik)
    log.debug("filings_watched", **watch_summary(report))


async def backfill_filings(context: TaskContext, task: Task) -> None:
    """One company's filing history back to the payload's `since` date (E3-S5)."""
    cik, since = _cik(task), _since(task)
    watcher = FilingsWatcher(context.fetcher, context.connect, context.kinds)
    report = await watcher.backfill(cik, since)
    log.debug("filings_backfill_done", cik=cik, pages=len(report.pages), inserted=report.inserted)


async def extract_earnings(context: TaskContext, task: Task) -> None:
    """One earnings release (E7a), routed from a new 8-K with item 2.02 or queued by the
    earnings backfill: its exhibits stored, its values extracted, verified and stored. A broken
    overrides file refuses the task: retrying can't help until the file is fixed."""
    cik, accession_no = _cik(task), _accession(task)
    try:
        overrides = read_overrides(context.settings.extraction_overrides_file)
    except OverrideError as exc:
        raise TaskRefused(str(exc)) from exc
    await EarningsIngest(context.fetcher, context.connect, overrides).run(cik, accession_no)


async def backfill_earnings(context: TaskContext, task: Task) -> None:
    """One company's earnings releases back to the payload's `since` date (E7a-S5): queues an
    earnings_extract task for each, after loading its predecessors' filings."""
    cik, since = _cik(task), _since(task)
    backfill = EarningsBackfill(context.fetcher, context.connect, context.kinds)
    await backfill.run(cik, since, force=task.payload.get("force") is True)


async def refresh_facts(context: TaskContext, task: Task) -> None:
    """One company's XBRL facts (E4a), routed from a new 10-K or 10-Q or queued by the facts
    backfill: its companyfacts and its predecessors' stored and loaded. A new filing the SEC's
    file doesn't list yet makes the task retry (FactsNotPublished)."""
    await FactsLoader(context.fetcher, context.connect).refresh(_cik(task))


def _accession(task: Task) -> str:
    accession = task.payload.get("accession_no")
    try:
        return urls.accession_no(accession) if isinstance(accession, str) else _no_accession(task)
    except ValueError:
        return _no_accession(task)


def _no_accession(task: Task) -> str:
    raise TaskRefused(f"the task's payload has no accession number: {task.payload}")


def _since(task: Task) -> date:
    since = task.payload.get("since")
    try:
        return date.fromisoformat(since) if isinstance(since, str) else _no_since(task)
    except ValueError:
        return _no_since(task)


def _no_since(task: Task) -> date:
    raise TaskRefused(f"the task's payload has no since date (YYYY-MM-DD): {task.payload}")


def _cik(task: Task) -> int:
    cik = task.payload.get("cik")
    if isinstance(cik, bool) or not isinstance(cik, int) or cik <= 0:
        raise TaskRefused(f"the task's payload has no CIK: {task.payload}")
    return cik


def build_registry() -> Registry:
    """Every kind the worker runs. The watcher routes work only to these (PLAN.md §4)."""
    registry = Registry()
    registry.register(SECURITY_MASTER, load_security_master)
    registry.register(FILINGS_WATCH, watch_filings)
    registry.register(FILINGS_BACKFILL, backfill_filings)
    registry.register(EARNINGS_EXTRACT, extract_earnings)
    registry.register(EARNINGS_BACKFILL, backfill_earnings)
    registry.register(FACTS_REFRESH, refresh_facts)
    return registry
