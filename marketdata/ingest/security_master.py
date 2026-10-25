"""Load the security master (E2): every ticker from both SEC lists, and name, details and class
from each universe company's submissions JSON (PLAN.md §9.1-§9.2).

Every SEC file goes through RawFetcher, so the raw file is stored before it is parsed. All
fetching comes first; the database then changes in one transaction that holds an advisory
lock, so two loads never interleave and a load that fails half-way changes nothing. A load
with the same SEC files changes no row (E2-S2).
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import companies, securities
from marketdata.db.repositories.companies import Company
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.security_master import (
    FILER_TYPES,
    Classification,
    CompanyDetails,
    FilerType,
    SecFileError,
    SecurityChanges,
    TickerSnapshot,
    UniverseCompany,
    build_snapshot,
    classify_filer,
    diff_securities,
    parse_exchange_list,
    parse_fund_list,
    parse_submissions,
    plan_companies,
)
from marketdata.normalize.universe import (
    DataFileError,
    SecurityOverride,
    UniverseEntry,
    UniverseMatch,
    match_universe,
    parse_security_overrides,
    parse_universe,
)
from marketdata.sec import urls
from marketdata.sec.client import SecError
from marketdata.sec.raw_store import RawStoreError

log = structlog.get_logger(__name__)


def read_data_files(
    universe_file: Path, overrides_file: Path
) -> tuple[list[UniverseEntry], list[SecurityOverride]]:
    """The universe and the security overrides, checked; DataFileError lists every problem."""
    universe = parse_universe(_read(universe_file), str(universe_file))
    overrides = parse_security_overrides(_read(overrides_file), str(overrides_file))
    problems = []
    for override in overrides:
        parsed = urls.parse(override.source_url)
        if parsed is None or parsed.endpoint != "filing":
            problems.append(
                f"line {override.line}: {override.ticker}'s source_url is not an SEC filing "
                f"page: {override.source_url}"
            )
    if problems:
        raise DataFileError(str(overrides_file), problems)
    return universe, overrides


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DataFileError(str(path), ["file not found"]) from None


@dataclass(frozen=True)
class MemberReport:
    """A universe company after a load."""

    cik: int
    tickers: tuple[str, ...]  # its tickers in the universe file
    name: str | None
    classification: Classification
    error: str | None  # why its submissions could not be loaded


@dataclass(frozen=True)
class LoadReport:
    as_of: date
    snapshot: TickerSnapshot
    securities: SecurityChanges
    companies_added: int
    companies_changed: int
    universe: UniverseMatch
    members: tuple[MemberReport, ...]

    @property
    def failures(self) -> tuple[MemberReport, ...]:
        return tuple(member for member in self.members if member.error is not None)

    @property
    def class_counts(self) -> dict[FilerType, int]:
        return _class_counts(member.classification.filer_type for member in self.members)

    def lines(self, universe_file: str) -> list[str]:
        """The summary `marketdata ingest security-master` prints."""
        snapshot, changes = self.snapshot, self.securities
        lines = [
            f"Ticker lists of {self.as_of}: {snapshot.exchange_rows:,} exchange rows and "
            f"{snapshot.fund_rows:,} fund rows give {len(snapshot.listings):,} tickers.",
        ]
        for skipped in snapshot.skipped:
            lines.append(
                f"  Skipped {skipped.text!r} ({skipped.source} list, CIK {skipped.cik}): "
                f"{skipped.reason}"
            )
        for collision in snapshot.collisions:
            lines.append(
                f"  {collision.ticker} is in both lists: kept CIK {collision.kept.cik} from the "
                f"{collision.kept.source} list, dropped CIK {collision.dropped_cik} from the "
                f"{collision.dropped_source} list"
            )
        for override in snapshot.unused_overrides:
            lines.append(f"  Override for {override.ticker} unused: no list has that ticker")
        lines.append(
            f"Securities: {len(changes.opened):,} opened, {len(changes.updated):,} updated, "
            f"{changes.unchanged:,} unchanged."
        )
        if changes.stale:
            lines.append(
                f"  {len(changes.stale):,} current rows no longer match the lists and were "
                "left as they are."
            )
        lines.append(
            f"Companies: {self.companies_added:,} added, {self.companies_changed:,} changed."
        )
        lines.extend(_universe_lines(universe_file, self.universe, self.members))
        return lines


class SecurityMasterLoader:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def load(
        self,
        universe: Sequence[UniverseEntry],
        overrides: Sequence[SecurityOverride],
        *,
        as_of: date,
    ) -> LoadReport:
        _, exchange_data = await self._fetcher.fetch_url(urls.COMPANY_TICKERS_EXCHANGE)
        _, fund_data = await self._fetcher.fetch_url(urls.COMPANY_TICKERS_MF)
        snapshot = build_snapshot(
            parse_exchange_list(exchange_data), parse_fund_list(fund_data), overrides
        )
        listed = {ticker: listing.cik for ticker, listing in snapshot.listings.items()}
        match = match_universe(universe, listed)

        members: dict[int, UniverseCompany] = {}
        errors: dict[int, str] = {}
        for cik in match.ciks:
            listed_company = snapshot.companies.get(cik)
            in_fund_list = listed_company is not None and listed_company.in_fund_list
            details, error = await self._details(cik)
            if error is not None:
                errors[cik] = error
            members[cik] = UniverseCompany(
                cik, details, classify_filer(details, in_fund_list=in_fund_list)
            )

        async with self._connect() as conn, conn.transaction():
            await securities.lock_for_load(conn)
            stored = {company.cik: company for company in await companies.list_all(conn)}
            added, changed = plan_companies(stored, snapshot.companies, members)
            changes = diff_securities(await securities.list_current(conn), snapshot.listings)
            await companies.upsert(conn, [*added, *changed])
            await securities.insert(conn, changes.opened, as_of)
            await securities.update(conn, changes.updated)

        names = {row.cik: row.name for row in [*added, *changed]}
        report = LoadReport(
            as_of=as_of,
            snapshot=snapshot,
            securities=changes,
            companies_added=len(added),
            companies_changed=len(changed),
            universe=match,
            members=_members(match, members, errors, names, stored),
        )
        log.info(
            "security_master_loaded",
            tickers=len(snapshot.listings),
            opened=len(changes.opened),
            updated=len(changes.updated),
            companies_added=len(added),
            companies_changed=len(changed),
            universe=len(match.ciks),
            failures=len(errors),
        )
        return report

    async def _details(self, cik: int) -> tuple[CompanyDetails | None, str | None]:
        try:
            _, data = await self._fetcher.fetch_url(urls.submissions(cik))
            details = parse_submissions(data)
        except (SecError, RawStoreError, SecFileError) as exc:
            log.warning("submissions_not_loaded", cik=cik, error=str(exc))
            return None, str(exc)
        if details.cik != cik:
            error = f"submissions for CIK {cik} describe CIK {details.cik}"
            log.warning("submissions_not_loaded", cik=cik, error=error)
            return None, error
        return details, None


def _members(
    match: UniverseMatch,
    members: Mapping[int, UniverseCompany],
    errors: Mapping[int, str],
    written_names: Mapping[int, str | None],
    stored: Mapping[int, Company],
) -> tuple[MemberReport, ...]:
    tickers: dict[int, list[str]] = {}
    for entry, cik in match.matched:
        tickers.setdefault(cik, []).append(entry.ticker)
    for entry, closed in match.closed:
        tickers.setdefault(closed.cik, []).append(entry.ticker)
    reports = []
    for cik in match.ciks:
        member = members[cik]
        name = written_names.get(cik, stored[cik].name if cik in stored else None)
        reports.append(
            MemberReport(cik, tuple(tickers[cik]), name, member.classification, errors.get(cik))
        )
    return tuple(reports)


@dataclass(frozen=True)
class UniverseCheck:
    """The universe file against the stored security master, without calling the SEC."""

    universe: UniverseMatch
    stored: Mapping[int, Company]
    flagged: tuple[int, ...]  # CIKs stored with in_universe = true

    @property
    def not_loaded(self) -> list[int]:
        """Universe companies a load has not marked yet."""
        return [
            cik
            for cik in self.universe.ciks
            if cik not in self.stored or not self.stored[cik].in_universe
        ]

    @property
    def dropped(self) -> list[int]:
        """Companies still marked as in the universe that the file no longer lists."""
        listed = set(self.universe.ciks)
        return [cik for cik in self.flagged if cik not in listed]

    def lines(self, universe_file: str) -> list[str]:
        members = []
        for entry, cik in self.universe.matched:
            company = self.stored.get(cik)
            filer_type: FilerType = company.filer_type if company else "unknown"
            members.append(
                MemberReport(
                    cik,
                    (entry.ticker,),
                    company.name if company else None,
                    Classification(filer_type, "as stored"),
                    None,
                )
            )
        lines = _universe_lines(universe_file, self.universe, tuple(members))
        if self.not_loaded or self.dropped:
            lines.append(
                f"  {len(self.not_loaded)} companies to add and {len(self.dropped)} to remove: "
                "run `marketdata ingest security-master`"
            )
        return lines


async def check_universe(
    conn: AsyncConnection[Any], universe: Sequence[UniverseEntry]
) -> UniverseCheck:
    current = await securities.find_current(conn, [entry.ticker for entry in universe])
    match = match_universe(universe, {row.ticker: row.cik for row in current})
    stored = {company.cik: company for company in await companies.get_many(conn, match.ciks)}
    flagged = tuple(company.cik for company in await companies.list_in_universe(conn))
    return UniverseCheck(match, stored, flagged)


def _universe_lines(
    universe_file: str, match: UniverseMatch, members: Sequence[MemberReport]
) -> list[str]:
    count = len(match.matched) + len(match.closed) + len(match.unknown)
    lines = [f"Universe ({universe_file}): {count} tickers, {len(match.ciks)} companies."]
    for member in members:
        lines.append(
            f"  {', '.join(member.tickers):<12} {member.cik:>10}  "
            f"{member.classification.filer_type:<22} {member.name or '(no name)'}: "
            f"{member.classification.reason}"
        )
        if member.error is not None:
            lines.append(f"  {'':<12} {'':>10}  submissions not loaded: {member.error}")
    for cik, shared in match.shared.items():
        lines.append(f"  Same company through {', '.join(shared)} (CIK {cik})")
    unknown = ", ".join(entry.ticker for entry in match.unknown) or "none"
    lines.append(f"  Unknown tickers (in no SEC list): {unknown}")
    counts = _class_counts(member.classification.filer_type for member in members)
    lines.append(
        "  Classes: " + ", ".join(f"{filer_type} {count}" for filer_type, count in counts.items())
    )
    return lines


def _class_counts(filer_types: Iterable[FilerType]) -> dict[FilerType, int]:
    counts = Counter(filer_types)
    return {filer_type: counts.get(filer_type, 0) for filer_type in FILER_TYPES}
