"""Curated 13F managers (E9-S1): data/managers.csv loaded into `managers`.

`marketdata managers load` reads the file, checked line by line before anything is fetched
(PLAN.md §9.8: every CIK is looked up on EDGAR, never guessed). Then, for each manager, it
fetches its submissions JSON through the SEC client, raw first: the file must describe that CIK,
and the CIK must have filed a 13F-HR. EDGAR's name for it is stored as filed. The managers that
pass are written, curated, in one transaction; a manager the file no longer lists stays, not
curated, so the holdings it filed keep their manager. A manager that fails is reported and keeps
what was stored for it; the others are written.

The filings watcher picks the curated managers up from the table: the scheduler watches them
with the universe, and their new 13F reports are routed to `f13_parse` (§4).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog

from marketdata.db.repositories import managers
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.filings import parse_submissions
from marketdata.normalize.security_master import SecFileError
from marketdata.normalize.universe import DataFileError, ManagerEntry, parse_managers
from marketdata.sec import urls
from marketdata.sec.client import SecNotFound

log = structlog.get_logger(__name__)


def read_managers(path: Path) -> list[ManagerEntry]:
    """The curated managers file, checked; DataFileError lists every problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DataFileError(str(path), ["file not found"]) from None
    return parse_managers(text, str(path))


@dataclass(frozen=True)
class ManagerCheck:
    """A manager of the file after its submissions were checked."""

    entry: ManagerEntry
    name: str | None  # EDGAR's
    latest_period: date | None  # of its newest 13F-HR in the recent filings
    latest_filed: date | None
    error: str | None  # why it was not written


@dataclass(frozen=True)
class ManagersReport:
    checks: tuple[ManagerCheck, ...]
    written: int  # rows that changed
    uncurated: tuple[int, ...]  # CIKs the file no longer lists

    @property
    def failures(self) -> list[ManagerCheck]:
        return [check for check in self.checks if check.error is not None]


class ManagersLoader:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def load(self, entries: Sequence[ManagerEntry]) -> ManagersReport:
        """Check each manager on EDGAR, then curate exactly the file's managers."""
        checks = [await self._check(entry) for entry in entries]
        passed = [
            CuratedManager(
                check.entry.manager_cik, check.name, check.entry.display_name, check.entry.notes
            )
            for check in checks
            if check.error is None and check.name is not None
        ]
        async with self._connect() as conn, conn.transaction():
            written, uncurated = await managers.replace_curated(
                conn, [entry.manager_cik for entry in entries], passed
            )
        report = ManagersReport(tuple(checks), written, tuple(uncurated))
        log.info(
            "managers_loaded",
            listed=len(entries),
            written=written,
            failed=[check.entry.manager_cik for check in report.failures],
            uncurated=list(uncurated),
        )
        return report

    async def _check(self, entry: ManagerEntry) -> ManagerCheck:
        cik = entry.manager_cik
        try:
            _, data = await self._fetcher.fetch_url(urls.submissions(cik))
            submissions = parse_submissions(data)
        except SecNotFound:
            return ManagerCheck(entry, None, None, None, f"EDGAR has no CIK {cik}")
        except SecFileError as exc:
            return ManagerCheck(entry, None, None, None, str(exc))
        if submissions.cik != cik:
            return ManagerCheck(
                entry, None, None, None, f"the submissions of CIK {cik} describe {submissions.cik}"
            )
        name = submissions.name or None
        reports = [row for row in submissions.filings if row.form == "13F-HR"]
        if not reports:
            return ManagerCheck(
                entry,
                name,
                None,
                None,
                f"CIK {cik} ({name or 'no name'}) has filed no 13F-HR in its recent filings: "
                "check the CIK on EDGAR",
            )
        if name is None:
            return ManagerCheck(entry, None, None, None, f"EDGAR gives CIK {cik} no name")
        latest = max(reports, key=lambda row: (row.filed_at, row.accession_no))
        return ManagerCheck(entry, name, latest.period_of_report, latest.filed_at, None)
