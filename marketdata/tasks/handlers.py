"""The registered task kinds and their handlers (E3-S3 onward).

Handlers are thin wrappers around ingest/: they turn what an ingest step reports into success,
a retry (an exception) or TaskRefused, so ingest/ never depends on the queue.
"""

from datetime import UTC, datetime

import structlog

from marketdata.db.repositories.tasks import Task
from marketdata.ingest.security_master import SecurityMasterLoader, read_data_files
from marketdata.ingest.watcher import FilingsWatcher, watch_summary
from marketdata.normalize.security_master import SnapshotRefused
from marketdata.normalize.universe import DataFileError
from marketdata.tasks.kinds import FILINGS_WATCH, SECURITY_MASTER
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
    except DataFileError as exc:
        raise TaskRefused(str(exc)) from exc
    loader = SecurityMasterLoader(context.fetcher, context.connect)
    try:
        report = await loader.load(universe, overrides, as_of=datetime.now(UTC).date())
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
    return registry
