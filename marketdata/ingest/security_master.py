"""Load the security master (E2): every ticker from both SEC lists, and name, details and class
from each universe company's submissions JSON (PLAN.md §9.1-§9.2). Predecessors of universe
companies (data/predecessors.csv, D-068) are loaded the same way, out of the universe, with
their links.

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

from marketdata.db.repositories import companies, predecessors, securities
from marketdata.db.repositories.companies import Company
from marketdata.db.repositories.predecessors import CompanyPredecessor
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.security_master import (
    FILER_TYPES,
    Classification,
    ClosedSecurity,
    CompanyDetails,
    FilerType,
    SecFileError,
    SecurityChanges,
    TickerSnapshot,
    UniverseCompany,
    build_snapshot,
    check_snapshot,
    classify_filer,
    diff_securities,
    parse_exchange_list,
    parse_fund_list,
    parse_submissions,
    plan_companies,
)
from marketdata.normalize.universe import (
    ClosedTicker,
    DataFileError,
    Predecessor,
    SecurityOverride,
    UniverseEntry,
    UniverseMatch,
    match_universe,
    parse_predecessors,
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


def read_predecessors(path: Path) -> list[Predecessor]:
    """The predecessor links, checked; each source_url must be an SEC filing page."""
    links = parse_predecessors(_read(path), str(path))
    problems = []
    for link in links:
        parsed = urls.parse(link.source_url)
        if parsed is None or parsed.endpoint != "filing":
            problems.append(
                f"line {link.line}: CIK {link.predecessor_cik}'s source_url is not an SEC filing "
                f"page: {link.source_url}"
            )
    if problems:
        raise DataFileError(str(path), problems)
    return links


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
class PredecessorReport:
    """A predecessor after a load: its company's CIK, and its own row as a member."""

    cik: int
    predecessor: MemberReport
    linked: bool  # False when its submissions have never loaded, so it has no companies row


@dataclass(frozen=True)
class LoadReport:
    as_of: date
    snapshot: TickerSnapshot
    securities: SecurityChanges
    companies_added: int
    companies_changed: int
    universe: UniverseMatch
    members: tuple[MemberReport, ...]
    predecessors: tuple[PredecessorReport, ...] = ()

    @property
    def failures(self) -> tuple[MemberReport, ...]:
        loaded = [*self.members, *(link.predecessor for link in self.predecessors)]
        return tuple(member for member in loaded if member.error is not None)

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
            f"Securities: {len(changes.opened):,} opened, {len(changes.closed):,} closed, "
            f"{len(changes.updated):,} updated, {changes.unchanged:,} unchanged."
        )
        moved = [f"{c.row.ticker} from CIK {c.row.cik} to CIK {c.moved_to}" for c in changes.moved]
        ended = [f"{c.row.ticker} (CIK {c.row.cik})" for c in changes.closed if c.moved_to is None]
        lines.extend(_listed("Moved to another CIK", moved))
        lines.extend(_listed("No longer listed", ended))
        lines.extend(
            _listed(
                "Opened and ended today, so deleted",
                [f"{row.ticker} (CIK {row.cik})" for row in changes.deleted],
            )
        )
        lines.append(
            f"Companies: {self.companies_added:,} added, {self.companies_changed:,} changed."
        )
        lines.extend(_universe_lines(universe_file, self.universe, self.members, changes.moved))
        lines.extend(_predecessor_lines(self.predecessors))
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
        links: Sequence[Predecessor],
        force: bool = False,
    ) -> LoadReport:
        """Load the lists of `as_of`, and the predecessors in `links`: the stored links become
        exactly these. `force` applies a snapshot that ends more tickers than check_snapshot
        allows; raises SnapshotRefused otherwise, changing nothing."""
        _, exchange_data = await self._fetcher.fetch_url(urls.COMPANY_TICKERS_EXCHANGE)
        _, fund_data = await self._fetcher.fetch_url(urls.COMPANY_TICKERS_MF)
        snapshot = build_snapshot(
            parse_exchange_list(exchange_data), parse_fund_list(fund_data), overrides
        )
        listed = {ticker: listing.cik for ticker, listing in snapshot.listings.items()}
        unlisted = [entry.ticker for entry in universe if entry.ticker not in listed]
        match = match_universe(universe, listed, await self._history(unlisted, as_of))

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
        earlier: dict[int, UniverseCompany] = {}
        for link in links:
            cik = link.predecessor_cik
            if cik in members or cik in earlier:
                continue
            details, error = await self._details(cik)
            if error is not None:
                errors[cik] = error
            listed_company = snapshot.companies.get(cik)
            in_fund_list = listed_company is not None and listed_company.in_fund_list
            earlier[cik] = UniverseCompany(
                cik, details, classify_filer(details, in_fund_list=in_fund_list)
            )

        async with self._connect() as conn, conn.transaction():
            await securities.lock_for_load(conn)
            current = await securities.list_current(conn)
            changes = diff_securities(current, snapshot.listings, as_of)
            check_snapshot(current, changes, as_of, force=force)
            stored = {company.cik: company for company in await companies.list_all(conn)}
            added, changed = plan_companies(stored, snapshot.companies, members, earlier)
            await companies.upsert(conn, [*added, *changed])
            known = set(stored) | {row.cik for row in added}
            linked = [
                CompanyPredecessor(
                    cik=link.cik,
                    predecessor_cik=link.predecessor_cik,
                    source_url=link.source_url,
                    note=link.note,
                )
                for link in links
                if link.cik in known and link.predecessor_cik in known
            ]
            await predecessors.replace_all(conn, linked)
            # Old rows end before new ones open: one current row per ticker.
            await securities.delete(conn, [row.security_id for row in changes.deleted])
            await securities.close(conn, [c.row.security_id for c in changes.closed], as_of)
            await securities.update(conn, changes.updated)
            await securities.insert(conn, changes.opened, as_of)

        names = {row.cik: row.name for row in [*added, *changed]}
        report = LoadReport(
            as_of=as_of,
            snapshot=snapshot,
            securities=changes,
            companies_added=len(added),
            companies_changed=len(changed),
            universe=match,
            members=_members(match, members, errors, names, stored),
            predecessors=_predecessors(
                links, {**members, **earlier}, errors, names, stored, linked
            ),
        )
        log.info(
            "security_master_loaded",
            tickers=len(snapshot.listings),
            opened=len(changes.opened),
            closed=len(changes.closed),
            moved=len(changes.moved),
            deleted=len(changes.deleted),
            updated=len(changes.updated),
            companies_added=len(added),
            companies_changed=len(changed),
            universe=len(match.ciks),
            failures=len(errors),
        )
        return report

    async def _history(self, tickers: Sequence[str], as_of: date) -> dict[str, ClosedTicker]:
        """Where universe tickers that no list has went: their latest stored rows. A row still
        current ends with this load, on `as_of`."""
        if not tickers:
            return {}
        async with self._connect() as conn:
            rows = await securities.latest(conn, tickers)
        return {row.ticker: ClosedTicker(row.cik, row.valid_to or as_of) for row in rows}

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


def _predecessors(
    links: Sequence[Predecessor],
    loaded: Mapping[int, UniverseCompany],
    errors: Mapping[int, str],
    written_names: Mapping[int, str | None],
    stored: Mapping[int, Company],
    linked: Sequence[CompanyPredecessor],
) -> tuple[PredecessorReport, ...]:
    stored_links = {(link.cik, link.predecessor_cik) for link in linked}
    reports = []
    for link in links:
        cik = link.predecessor_cik
        name = written_names.get(cik, stored[cik].name if cik in stored else None)
        member = MemberReport(cik, (), name, loaded[cik].classification, errors.get(cik))
        reports.append(PredecessorReport(link.cik, member, (link.cik, cik) in stored_links))
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
        found = [*self.universe.matched, *((e, c.cik) for e, c in self.universe.closed)]
        for entry, cik in found:
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
    listed = {row.ticker: row.cik for row in current}
    unlisted = [entry.ticker for entry in universe if entry.ticker not in listed]
    closed = {
        row.ticker: ClosedTicker(row.cik, row.valid_to)
        for row in await securities.latest(conn, unlisted)
        if row.valid_to is not None
    }
    match = match_universe(universe, listed, closed)
    stored = {company.cik: company for company in await companies.get_many(conn, match.ciks)}
    flagged = tuple(company.cik for company in await companies.list_in_universe(conn))
    return UniverseCheck(match, stored, flagged)


def _universe_lines(
    universe_file: str,
    match: UniverseMatch,
    members: Sequence[MemberReport],
    moved: Sequence[ClosedSecurity] = (),
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
    for entry, closed in match.closed:
        lines.append(
            f"  {entry.ticker} is no longer listed (since {closed.valid_to}); CIK {closed.cik} "
            f"stays in the universe until {universe_file} changes"
        )
    tickers = {entry.ticker for entry, _ in match.matched}
    for move in moved:
        if move.row.ticker in tickers:
            lines.append(
                f"  {move.row.ticker} moved from CIK {move.row.cik} to CIK {move.moved_to}; the "
                f"universe follows the ticker: check {universe_file}"
            )
    unknown = ", ".join(entry.ticker for entry in match.unknown) or "none"
    lines.append(f"  Unknown tickers (in no SEC list): {unknown}")
    counts = _class_counts(member.classification.filer_type for member in members)
    lines.append(
        "  Classes: " + ", ".join(f"{filer_type} {count}" for filer_type, count in counts.items())
    )
    return lines


def _predecessor_lines(links: Sequence[PredecessorReport]) -> list[str]:
    if not links:
        return []
    lines = [f"Predecessors: {len(links)}."]
    for link in links:
        member = link.predecessor
        lines.append(
            f"  {link.cik:>10} <- {member.cik:<10} {member.classification.filer_type:<22} "
            f"{member.name or '(no name)'}: {member.classification.reason}"
        )
        if member.error is not None:
            lines.append(f"  {'':>10}    {'':<10} submissions not loaded: {member.error}")
        if not link.linked:
            lines.append(f"  {'':>10}    {'':<10} not linked: the company is not stored yet")
    return lines


def _class_counts(filer_types: Iterable[FilerType]) -> dict[FilerType, int]:
    counts = Counter(filer_types)
    return {filer_type: counts.get(filer_type, 0) for filer_type in FILER_TYPES}


def _listed(title: str, items: Sequence[str], limit: int = 20) -> list[str]:
    if not items:
        return []
    shown = "; ".join(items[:limit])
    more = f"; and {len(items) - limit:,} more" if len(items) > limit else ""
    return [f"  {title} ({len(items):,}): {shown}{more}"]
