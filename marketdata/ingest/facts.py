"""XBRL companyfacts ingest into xbrl_facts (E4a-S1).

A refresh of one company (`facts_refresh`: routed from each new 10-K or 10-Q, and queued by
`marketdata backfill facts`):

1. For the company and each of its predecessors (D-068): fetches its companyfacts JSON and
   keeps the file as served in the raw store, every time, before parsing its us-gaap and dei
   facts (normalize/companyfacts.py). A predecessor is refreshed too because a filing can be
   listed under both CIKs while its facts sit under one (XOM's joint Q2 2026 10-Q).
2. Upserts the facts into xbrl_facts: new facts are inserted, a stored fact whose details moved
   is updated, and facts a file no longer lists stay stored.
3. Checks the group's XBRL 10-K and 10-Q filings of the last 30 days in `filings` against the
   files. companyfacts can lag a filing by a few minutes, and a refresh that is already running
   when the next filing is listed doesn't see it, so a filing that no file holds yet makes the
   task retry while the watcher stored it less than an hour ago (FactsNotPublished). An older
   one is logged and the refresh completes: retrying every hour wouldn't bring it.

A company without companyfacts (404) has no facts yet: logged, not retried.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from marketdata.db.repositories import filings, predecessors, xbrl_facts
from marketdata.db.repositories.xbrl_facts import FactsWritten
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.companyfacts import CompanyFacts, parse_companyfacts
from marketdata.normalize.filings import PERIODIC_FORMS
from marketdata.normalize.security_master import SecFileError
from marketdata.sec import urls
from marketdata.sec.client import SecNotFound

log = structlog.get_logger(__name__)

RECENT_FILINGS = timedelta(days=30)  # filings checked against the files
PUBLISH_WAIT = timedelta(hours=1)  # how long a filing missing from the files is waited for


class FactsNotPublished(Exception):
    """A new filing isn't in the SEC's companyfacts yet; the task is retried."""


@dataclass(frozen=True)
class LoadedFile:
    cik: int
    raw_key: str | None  # None: the SEC has no companyfacts for this CIK
    facts: int  # facts in the file
    written: FactsWritten | None
    skipped: int = 0
    conflicts: int = 0


@dataclass(frozen=True)
class FactsReport:
    cik: int
    files: tuple[LoadedFile, ...]  # the company's, then its predecessors'
    missing: tuple[str, ...]  # recent filings that no file holds


class FactsLoader:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def refresh(self, cik: int, *, now: datetime | None = None) -> FactsReport:
        """Load the facts of `cik` and its predecessors (steps 1-3 above)."""
        now = now or datetime.now(UTC)
        async with self._connect() as conn:
            group = [cik, *await predecessors.predecessors_of(conn, cik)]
        loaded: list[LoadedFile] = []
        held: set[str] = set()
        for member in group:
            file, parsed = await self._load(member)
            loaded.append(file)
            if parsed is not None:
                held |= parsed.accessions
        missing = await self._missing(group, held, now)
        waiting = [accession for accession, stored in missing if now - stored < PUBLISH_WAIT]
        if waiting:
            raise FactsNotPublished(
                f"companyfacts of CIK {cik} doesn't list {', '.join(waiting)} yet"
            )
        for accession, stored in missing:
            log.warning(
                "facts_filing_missing",
                cik=cik,
                accession_no=accession,
                listed_at=stored.isoformat(),
            )
        return FactsReport(cik, tuple(loaded), tuple(accession for accession, _ in missing))

    async def _load(self, cik: int) -> tuple[LoadedFile, CompanyFacts | None]:
        try:
            key, data = await self._fetcher.fetch_url(urls.companyfacts(cik))
        except SecNotFound:
            log.warning("companyfacts_not_found", cik=cik)
            return LoadedFile(cik, None, 0, None), None
        parsed = parse_companyfacts(data)
        if parsed.cik != cik:
            raise SecFileError(f"companyfacts for CIK {cik} describe CIK {parsed.cik}")
        async with self._connect() as conn, conn.transaction():
            written = await xbrl_facts.upsert(conn, parsed.facts)
        for moved in written.moved:
            log.warning(
                "xbrl_fact_value_moved",
                cik=cik,
                concept=f"{moved.taxonomy}:{moved.concept}",
                unit=moved.unit,
                period_end=moved.period_end.isoformat(),
                accession_no=moved.accession_no,
                old=str(moved.old_value),
                new=str(moved.new_value),
            )
        for conflict in parsed.conflicts:
            log.warning("companyfacts_conflict", cik=cik, raw_key=key, fact=conflict)
        log.info(
            "facts_loaded",
            cik=cik,
            raw_key=key,
            facts=len(parsed.facts),
            inserted=written.inserted,
            updated=written.updated,
            skipped=parsed.skipped,
            repeated=parsed.repeated,
            conflicts=len(parsed.conflicts),
            other_taxonomies=dict(parsed.other_taxonomies),
        )
        file = LoadedFile(
            cik, key, len(parsed.facts), written, parsed.skipped, len(parsed.conflicts)
        )
        return file, parsed

    async def _missing(
        self, group: Sequence[int], held: set[str], now: datetime
    ) -> list[tuple[str, datetime]]:
        """The group's recent XBRL 10-K and 10-Q filings that no file holds, with when the
        watcher stored each (a filing listed under two CIKs counts once)."""
        since = (now - RECENT_FILINGS).date()
        missing: dict[str, datetime] = {}
        async with self._connect() as conn:
            for cik in group:
                listed = await filings.list_for_company(
                    conn, cik, forms=sorted(PERIODIC_FORMS), since=since, limit=100
                )
                for filing in listed:
                    if filing.is_xbrl and filing.accession_no not in held:
                        missing.setdefault(filing.accession_no, filing.discovered_at)
        return sorted(missing.items())
