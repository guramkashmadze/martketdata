"""Curated fund portfolios (PLAN.md §9.8, §10.2, E9): what well-known investors reported holding
at a quarter end in their 13F filings, and how that compares with the quarter before.

- `list_funds`: the curated managers, each with its latest report.
- `fund_holdings`: a manager's positions at a quarter end (its latest by default), largest value
  first, then the positions it no longer reports, each with its change from the quarter before.
- `company_funds`: the curated managers holding a company's shares in their latest report, by
  name.

Positions are facts as filed, never advice (D-029): they are listed by value inside one
portfolio and by manager name across managers; nothing ranks managers. Change types are decided
when read (D-124): from the manager's report for the quarter before, with a split applied when
other holders confirm it; without that report there is none, flagged `no_prior_report`.
`unit_suspect` (D-023) is decided when read too, among the curated managers holding the same
security that quarter. A 13F lists long positions in US-listed securities (and options on them)
at the quarter end, filed up to 45 days later: every answer gives the report period and the
filing date. CUSIPs are served only as filed (D-121).
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import cusips, holdings, managers, xbrl_facts
from marketdata.db.repositories.cusips import CusipLink
from marketdata.db.repositories.holdings import StoredF13Filing, StoredPosition
from marketdata.db.repositories.managers import Manager
from marketdata.normalize.form13f import METHOD
from marketdata.normalize.holdings import (
    SPLIT_ADJUSTED,
    UNIT_SUSPECT,
    HeldBoth,
    PositionChange,
    PricePoint,
    changes,
    parse_quarter,
    quarter_label,
    quarters_before,
    split_ratio,
    unit_suspects,
)
from marketdata.sec import urls
from marketdata.service.companies import group_of, name_words, resolve_ticker
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.pages import (
    DEFAULT_LIMIT,
    check_limit,
    decode_cursor,
    encode_cursor,
)
from marketdata.service.served import FlagRef, Number, Served, Source, newest

CHANGE_TYPES = ("new", "added", "trimmed", "unchanged", "exited")
NO_PRIOR_REPORT = "no_prior_report"
ABOUT_13F = (
    "A 13F report lists a manager's long positions in US-listed securities, and options on "
    "them, at the quarter end; it is filed up to 45 days later and leaves out short positions, "
    "cash and other assets."
)


class FundFiling(BaseModel):
    """A 13F report behind a quarter's positions (§9.10): the filing, its information table,
    the unit its values were read in and why, and the summary page's line count and total next
    to what the table holds."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str  # 13F-HR or 13F-HR/A
    amendment_type: str | None  # RESTATEMENT or NEW HOLDINGS
    filed_at: date
    filing_url: str
    table_url: str | None  # the information table's XML; none for a report without holdings
    value_unit: str  # dollars, or thousands as filed before 2023-01-03
    unit_rule: str
    entry_total: int | None  # the summary page's
    lines: int  # in the table
    value_total: Number | None  # the summary page's, as filed
    value_sum: Number  # the table's values added up, as filed
    method: str
    extractor_version: str


class Fund(BaseModel):
    """A curated manager and its latest report."""

    model_config = ConfigDict(frozen=True)

    manager_cik: int
    manager: str  # our name for it
    name: str  # EDGAR's
    curated: bool
    report_period: date | None  # its newest quarter end with a report
    quarter: str | None  # 2026Q2
    filed_at: date | None  # of the latest report for that quarter
    accession_no: str | None
    positions: int | None
    value_usd: Number | None  # every position, as reported
    long_value_usd: Number | None  # the long equity positions (shares held outright)


class FundList(BaseModel):
    model_config = ConfigDict(frozen=True)

    funds: tuple[Fund, ...]  # by our name for the manager
    note: str


class PositionProof(BaseModel):
    """Where a position comes from (§9.10): the reports whose lines it adds up (in `filings`
    or the sources, with their links, units and summary-page checks) and how they were read."""

    model_config = ConfigDict(frozen=True)

    accession_nos: tuple[str, ...]
    lines: int  # of the information tables, added up
    method: str  # f13_xml
    extractor_version: str


class FundPosition(BaseModel):
    """A position at the quarter end, as the 13F reports it, and its change from the quarter
    before."""

    model_config = ConfigDict(frozen=True)

    cusip: str  # as filed
    issuer: str | None  # as filed
    title_of_class: str | None
    ticker: str | None  # our security's, through its FIGI (D-121)
    company_cik: int | None
    figi: str | None
    put_call: str | None  # PUT or CALL: options are valued on their underlying
    share_type: str  # SH (shares) or PRN (principal amount)
    shares: Number  # 0 for a position the manager no longer reports
    value_usd: Number
    weight_pct: Number | None  # of the long equity positions; none for options and principal
    change_type: str | None  # new, added, trimmed, unchanged or exited; none without the
    # quarter before
    previous_shares: Number | None  # as filed the quarter before
    shares_change: Number | None  # split-adjusted when a split happened
    split_ratio: str | None  # new shares per old share: 10:1, or 1:10 for a reverse split
    quality_flags: tuple[str, ...]
    proof: PositionProof


class ChangeCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    change_type: str
    positions: int


class FundHoldings(BaseModel):
    model_config = ConfigDict(frozen=True)

    manager_cik: int
    manager: str
    name: str
    quarter: str
    report_period: date
    filed_at: date  # of the latest report for the quarter
    previous_period: date | None  # the quarter end compared with; null without a report
    filings: tuple[FundFiling, ...]  # in filing order: the report, then its amendments
    positions_count: int  # held at the quarter end
    value_usd: Number
    long_value_usd: Number
    changes: tuple[ChangeCount, ...]  # every position, in CHANGE_TYPES order
    positions: tuple[FundPosition, ...]  # a page: largest value first, then those exited
    next_cursor: str | None
    note: str


class Holder(BaseModel):
    """A curated manager's position in a company in its latest report."""

    model_config = ConfigDict(frozen=True)

    manager_cik: int
    manager: str
    quarter: str
    report_period: date
    filed_at: date
    accession_no: str  # its latest report for the quarter
    filing_url: str
    position: FundPosition


class CompanyFunds(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    cusips: tuple[str, ...]  # the company's CUSIPs found in 13F reports (as filed)
    holders: tuple[Holder, ...]  # by manager name; a position exited in the latest report too
    note: str


async def list_managers(conn: AsyncConnection[Any], *, curated: bool = True) -> list[Manager]:
    """The 13F managers by display name: the curated ones, or all with `curated=False`."""
    return await managers.list_managers(conn, curated=curated)


async def resolve_manager(conn: AsyncConnection[Any], text: str) -> Manager:
    """A curated manager by its CIK or its name: our display name or EDGAR's, as a whole, by its
    start, or by a word in it ("icahn", "pabrai"). InvalidParameter when several match;
    NotFound names the curated managers."""
    curated = await managers.list_managers(conn)
    value = text.strip()
    if value.isdigit():
        found = [manager for manager in curated if manager.manager_cik == int(value)]
        if found:
            return found[0]
        raise NotFound(f"CIK {int(value)} is not a curated manager. {_curated(curated)}")
    words = name_words(value)
    if not words:
        raise InvalidParameter(f"{text!r} is not a manager's name or CIK. {_curated(curated)}")
    named = [(manager, _names(manager)) for manager in curated]
    for test in (
        lambda name: name == words,
        lambda name: name.startswith(words),
        lambda name: f" {words}" in f" {name}",
    ):
        found = [manager for manager, names in named if any(test(name) for name in names)]
        if len(found) == 1:
            return found[0]
        if found:
            listed = ", ".join(f"{m.display_name} ({m.manager_cik})" for m in found)
            raise InvalidParameter(f"{text!r} matches {listed}: ask for one by name or CIK")
    raise NotFound(f"No curated manager matches {text!r}. {_curated(curated)}")


async def list_funds(conn: AsyncConnection[Any], *, curated: bool = True) -> Served[FundList]:
    """The curated managers (or all with `curated=False`) by name, each with its latest
    report: the quarter, the filing date and its positions' value."""
    listed = await managers.list_managers(conn, curated=curated)
    latest = {
        filing.manager_cik: filing
        for filing in await holdings.latest_filings(conn, [m.manager_cik for m in listed])
    }
    summary = {
        (s.manager_cik, s.report_period): s
        for s in await holdings.summaries(
            conn, [(cik, filing.report_period) for cik, filing in latest.items()]
        )
    }
    funds = []
    for manager in listed:
        filing = latest.get(manager.manager_cik)
        totals = summary.get((manager.manager_cik, filing.report_period)) if filing else None
        funds.append(
            Fund(
                manager_cik=manager.manager_cik,
                manager=manager.display_name,
                name=manager.name,
                curated=manager.curated,
                report_period=filing.report_period if filing else None,
                quarter=quarter_label(filing.report_period) if filing else None,
                filed_at=filing.filed_at if filing else None,
                accession_no=filing.accession_no if filing else None,
                positions=totals.positions if totals else None,
                value_usd=totals.value_usd if totals else None,
                long_value_usd=totals.long_value_usd if totals else None,
            )
        )
    return Served(
        data=FundList(funds=tuple(funds), note=ABOUT_13F),
        sources=_sources(latest.values()),
        as_of=newest(filing.parsed_at for filing in latest.values()),
        quality_flags=(),
    )


def check_changes(wanted: Sequence[str] | None) -> tuple[str, ...] | None:
    """Change types asked for; None for every one."""
    if not wanted:
        return None
    chosen = tuple(dict.fromkeys(name.strip().lower() for name in wanted if name.strip()))
    bad = [name for name in chosen if name not in CHANGE_TYPES]
    if bad or not chosen:
        raise InvalidParameter(
            f"change types are {', '.join(CHANGE_TYPES)}; got {', '.join(bad) or 'none'}"
        )
    return chosen


async def fund_holdings(
    conn: AsyncConnection[Any],
    manager: str | int,
    *,
    quarter: str | None = None,
    change: Sequence[str] | None = None,
    limit: int | None = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Served[FundHoldings]:
    """A curated manager's positions at a quarter end (its latest by default): held positions
    largest value first, then those exited, each with its change from the quarter before;
    `change` keeps only these change types. `limit=None` answers every position (the MCP
    tool composes its answer from them)."""
    if limit is not None:
        check_limit(limit)
    kinds = check_changes(change)
    fund = await resolve_manager(conn, str(manager))
    reported = await holdings.periods(conn, fund.manager_cik)
    if not reported:
        raise NotFound(f"{fund.display_name} has no 13F report stored yet")
    period = _quarter(quarter, reported, fund.display_name)
    previous = quarters_before(period, 1)
    has_previous = previous in reported
    filed = await holdings.period_filings(conn, fund.manager_cik, period)
    now = await holdings.positions(conn, fund.manager_cik, period)
    before = await holdings.positions(conn, fund.manager_cik, previous) if has_previous else []
    others = await holdings.positions_of(
        conn, sorted({p.cusip for p in [*now, *before]}), [previous, period]
    )
    held = sorted({p.cusip for p in [*now, *before]})  # exited positions are named too
    links = {link.cusip: link for link in await cusips.links(conn, held)}
    split_facts = await _reported_splits(conn, links, previous, period)
    earlier = (
        await holdings.period_filings(conn, fund.manager_cik, previous) if has_previous else []
    )
    versions = {f.accession_no: f.extractor_version for f in [*filed, *earlier]}
    served = _positions(period, now, before, others, links, has_previous, split_facts, versions)
    counts = tuple(
        ChangeCount(change_type=kind, positions=sum(1 for p in served if p.change_type == kind))
        for kind in CHANGE_TYPES
    )
    listed = [p for p in served if kinds is None or p.change_type in kinds]
    keys = [(p.cusip, p.put_call or "") for p in listed]
    start = 0
    if cursor:
        cusip, put_call = decode_cursor(cursor, 2)
        if (cusip, put_call) not in keys:
            raise InvalidParameter("cursor is not one this endpoint returned for this quarter")
        start = keys.index((cusip, put_call)) + 1
    end = len(listed) if limit is None else start + limit
    page = listed[start:end]
    more = end < len(listed)
    long_value = sum(
        (p.value_usd for p in now if p.put_call is None and p.share_type == "SH"), Decimal(0)
    )
    data = FundHoldings(
        manager_cik=fund.manager_cik,
        manager=fund.display_name,
        name=fund.name,
        quarter=quarter_label(period),
        report_period=period,
        filed_at=max(filing.filed_at for filing in filed),
        previous_period=previous if has_previous else None,
        filings=tuple(_filing(filing) for filing in filed),
        positions_count=len(now),
        value_usd=sum((p.value_usd for p in now), Decimal(0)),
        long_value_usd=long_value,
        changes=counts,
        positions=tuple(page),
        next_cursor=encode_cursor(*keys[end - 1]) if more else None,
        note=ABOUT_13F,
    )
    return Served(
        data=data,
        sources=_sources(filed),
        as_of=newest([*(f.parsed_at for f in filed), *(p.updated_at for p in now)]),
        quality_flags=tuple(
            FlagRef(code=code, metric="fund_position", period_end=period, ticker=p.ticker)
            for p in page
            for code in p.quality_flags
        ),
    )


async def company_funds(conn: AsyncConnection[Any], ticker: str) -> Served[CompanyFunds]:
    """The curated managers holding a company's shares (any of its classes, and its
    predecessors') in their latest report, by manager name, with those that exited it there."""
    security = await resolve_ticker(conn, ticker)
    found = await cusips.of_companies(conn, await group_of(conn, security.cik))
    curated = await managers.list_managers(conn)
    latest = {
        filing.manager_cik: filing
        for filing in await holdings.latest_filings(conn, [m.manager_cik for m in curated])
    }
    periods = sorted({f.report_period for f in latest.values()})
    befores = sorted({quarters_before(period, 1) for period in periods})
    rows = await holdings.positions_of(conn, found, sorted({*periods, *befores})) if found else []
    links = {link.cusip: link for link in await cusips.links(conn, found)}
    accessions = sorted({accession for row in rows for accession in row.source_accessions})
    versions = {
        f.accession_no: f.extractor_version for f in await holdings.filings(conn, accessions)
    }
    reported = {
        manager.manager_cik: set(await holdings.periods(conn, manager.manager_cik))
        for manager in curated
        if manager.manager_cik in latest
    }
    holders = []
    used: list[StoredF13Filing] = []
    for manager in curated:
        filing = latest.get(manager.manager_cik)
        if filing is None:
            continue
        period, previous = filing.report_period, quarters_before(filing.report_period, 1)
        now = [
            r for r in rows if r.manager_cik == manager.manager_cik and r.report_period == period
        ]
        before = [
            r for r in rows if r.manager_cik == manager.manager_cik and r.report_period == previous
        ]
        if not now and not before:
            continue
        has_previous = previous in reported[manager.manager_cik]
        splits = await _reported_splits(conn, links, previous, period)
        for position in _positions(
            period, now, before, rows, links, has_previous, splits, versions
        ):
            holders.append(
                Holder(
                    manager_cik=manager.manager_cik,
                    manager=manager.display_name,
                    quarter=quarter_label(period),
                    report_period=period,
                    filed_at=filing.filed_at,
                    accession_no=filing.accession_no,
                    filing_url=urls.filing_folder(filing.manager_cik, filing.accession_no),
                    position=position,
                )
            )
        used.append(filing)
    return Served(
        data=CompanyFunds(
            ticker=security.ticker,
            cik=security.cik,
            company=security.name,
            cusips=tuple(found),
            holders=tuple(holders),
            note=ABOUT_13F,
        ),
        sources=_sources(used),
        as_of=newest([*(f.parsed_at for f in used), *(r.updated_at for r in rows)]),
        quality_flags=tuple(
            FlagRef(
                code=code,
                metric="fund_position",
                period_end=holder.report_period,
                ticker=security.ticker,
            )
            for holder in holders
            for code in holder.position.quality_flags
        ),
    )


def _positions(
    period: date,
    now: Sequence[StoredPosition],
    before: Sequence[StoredPosition],
    everyone: Sequence[StoredPosition],
    links: Mapping[str, CusipLink],
    has_previous: bool,
    reported: Mapping[str, Fraction],
    versions: Mapping[str, str],
) -> list[FundPosition]:
    """One manager's positions at a quarter end with their changes, the cross-manager unit
    check and splits applied; then the positions of the quarter before it no longer reports.
    `reported`: the splits the issuers' own filings give for the quarter."""
    previous = quarters_before(period, 1)
    suspects = unit_suspects(
        PricePoint(r.manager_cik, r.cusip, r.put_call, r.share_type, r.value_usd, r.shares)
        for r in everyone
        if r.report_period == period
    )
    splits = _splits(everyone, period, previous, reported)
    found = (
        changes(
            {(p.cusip, p.put_call): p.shares for p in now},
            {(p.cusip, p.put_call): p.shares for p in before},
            splits,
        )
        if has_previous
        else {}
    )
    served = [
        _position(p, found.get((p.cusip, p.put_call)), links, suspects, has_previous, versions)
        for p in now
    ]
    held = {(p.cusip, p.put_call) for p in now}
    gone = sorted(  # a position of zero shares the quarter before has nothing to exit
        (p for p in before if (p.cusip, p.put_call) not in held and (p.cusip, p.put_call) in found),
        key=lambda p: (-p.value_usd, p.cusip, p.put_call or ""),
    )
    for old in gone:
        change = found[(old.cusip, old.put_call)]
        link = links.get(old.cusip)
        served.append(
            FundPosition(
                cusip=old.cusip,
                issuer=old.name_of_issuer,
                title_of_class=old.title_of_class,
                ticker=link.ticker if link else None,
                company_cik=link.cik if link else None,
                figi=_figi(old, link),
                put_call=old.put_call,
                share_type=old.share_type,
                shares=Decimal(0),
                value_usd=Decimal(0),
                weight_pct=None,
                change_type="exited",
                previous_shares=old.shares,
                shares_change=change.shares_change,
                split_ratio=_ratio(change.split_ratio),
                quality_flags=(SPLIT_ADJUSTED,) if change.split_ratio else (),
                proof=_proof(old, versions),
            )
        )
    return served


def _position(
    stored: StoredPosition,
    change: PositionChange | None,
    links: Mapping[str, CusipLink],
    suspects: set[tuple[int, tuple[str, str | None, str]]],
    has_previous: bool,
    versions: Mapping[str, str],
) -> FundPosition:
    link = links.get(stored.cusip)
    flags = list(stored.quality_flags)
    if (stored.manager_cik, (stored.cusip, stored.put_call, stored.share_type)) in suspects:
        flags.append(UNIT_SUSPECT)
    if change is not None and change.split_ratio is not None:
        flags.append(SPLIT_ADJUSTED)
    if not has_previous:
        flags.append(NO_PRIOR_REPORT)
    return FundPosition(
        cusip=stored.cusip,
        issuer=stored.name_of_issuer,
        title_of_class=stored.title_of_class,
        ticker=link.ticker if link else None,
        company_cik=link.cik if link else None,
        figi=_figi(stored, link),
        put_call=stored.put_call,
        share_type=stored.share_type,
        shares=stored.shares,
        value_usd=stored.value_usd,
        weight_pct=stored.weight_pct,
        change_type=change.change_type if change else None,
        previous_shares=change.previous_shares if change else None,
        shares_change=change.shares_change if change else None,
        split_ratio=_ratio(change.split_ratio) if change else None,
        quality_flags=tuple(flags),
        proof=_proof(stored, versions),
    )


async def _reported_splits(
    conn: AsyncConnection[Any], links: Mapping[str, CusipLink], previous: date, period: date
) -> dict[str, Fraction]:
    """The splits the issuers' XBRL facts report as taking effect in the quarter, for each of
    their CUSIPs (every share class splits alike). A company reporting two ratios for one
    quarter gets none."""
    by_cik: dict[int, list[str]] = {}
    for link in links.values():
        if link.cik is not None:
            by_cik.setdefault(link.cik, []).append(link.cusip)
    if not by_cik:
        return {}
    ratios: dict[int, set[Decimal]] = {}
    for cik, _, value in await xbrl_facts.splits(conn, list(by_cik), previous, period):
        ratios.setdefault(cik, set()).add(value)
    return {
        cusip: Fraction(next(iter(values)))
        for cik, values in ratios.items()
        if len(values) == 1
        for cusip in by_cik[cik]
    }


def _splits(
    everyone: Sequence[StoredPosition],
    period: date,
    previous: date,
    reported: Mapping[str, Fraction],
) -> dict[str, Fraction]:
    """Splits between the two quarter ends: the issuer's reported ratio, or the evidence of
    every curated manager holding the security outright at both, checked against the price."""
    by_cusip: dict[str, dict[int, dict[date, StoredPosition]]] = {}
    for row in everyone:
        if row.put_call is None and row.share_type == "SH":
            by_cusip.setdefault(row.cusip, {}).setdefault(row.manager_cik, {})[
                row.report_period
            ] = row
    found = {}
    for cusip, by_manager in by_cusip.items():
        both = [
            HeldBoth(
                cik,
                rows[previous].shares,
                rows[previous].value_usd,
                rows[period].shares,
                rows[period].value_usd,
            )
            for cik, rows in by_manager.items()
            if previous in rows and period in rows
        ]
        ratio = split_ratio(both, reported.get(cusip)) if both else None
        if ratio is not None:
            found[cusip] = ratio
    return found


def _proof(position: StoredPosition, versions: Mapping[str, str]) -> PositionProof:
    used = tuple(position.source_accessions)
    return PositionProof(
        accession_nos=used,
        lines=position.lines,
        method=METHOD,
        extractor_version=max((versions.get(accession, "") for accession in used), default=""),
    )


def _figi(position: StoredPosition, link: CusipLink | None) -> str | None:
    return link.figi if link and link.figi else position.figi


def _ratio(ratio: Fraction | None) -> str | None:
    return None if ratio is None else f"{ratio.numerator}:{ratio.denominator}"


def _quarter(text: str | None, reported: Sequence[date], manager: str) -> date:
    if text is None:
        return reported[0]
    try:
        period = parse_quarter(text)
    except ValueError as exc:
        raise InvalidParameter(str(exc)) from None
    if period not in reported:
        shown = ", ".join(quarter_label(p) for p in reported[:12])
        raise NotFound(f"{manager} has no 13F report for {quarter_label(period)}; it has {shown}")
    return period


def _filing(filing: StoredF13Filing) -> FundFiling:
    return FundFiling(
        accession_no=filing.accession_no,
        form=filing.form,
        amendment_type=filing.amendment_type,
        filed_at=filing.filed_at,
        filing_url=urls.filing_folder(filing.manager_cik, filing.accession_no),
        table_url=(
            urls.filing_document(filing.manager_cik, filing.accession_no, filing.table_doc)
            if filing.table_doc
            else None
        ),
        value_unit=filing.value_unit,
        unit_rule=filing.unit_rule,
        entry_total=filing.entry_total,
        lines=filing.lines,
        value_total=filing.value_total,
        value_sum=filing.value_sum,
        method=filing.method,
        extractor_version=filing.extractor_version,
    )


def _sources(filed: Iterable[StoredF13Filing]) -> tuple[Source, ...]:
    return tuple(
        Source(
            accession_no=filing.accession_no,
            form=filing.form,
            filed_at=filing.filed_at,
            url=urls.filing_folder(filing.manager_cik, filing.accession_no),
        )
        for filing in sorted(filed, key=lambda f: (f.filed_at, f.accession_no), reverse=True)
    )


def _names(manager: Manager) -> tuple[str, str]:
    return name_words(manager.display_name), name_words(manager.name)


def _curated(curated: list[Manager]) -> str:
    return "Curated managers: " + ", ".join(manager.display_name for manager in curated) + "."
