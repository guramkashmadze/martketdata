"""Insider trades (PLAN.md §9.7, §10.2, E8): the trades of Form 4 filings, with proof.

A trade is served from the filing that states it now: a line a 4/A supersedes is served from the
4/A's line that corrects or restates it, and a filing made twice is served once (E8-S3).

`insider_activity` (E8-S5) serves a company's trades of a window of days, by trade date: the
open-market or private purchases and sales (codes P and S) by default, or the codes asked for;
table I (shares) unless derivatives are asked for too. Trades are grouped per insider group,
day, code and security (§9.7, normalize/insiders.py), each with the filing's 10b5-1 box, its
footnotes and its proof: the filings and lines it sums. Totals per code and a summary per
insider cover the whole window; the trades come a page at a time, newest first. Nothing is
ranked (D-029): insiders are listed by their latest trade.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import insiders
from marketdata.db.repositories.insiders import StoredFiling, StoredLine, StoredOwner
from marketdata.normalize.insider_data_set import OurFiling
from marketdata.normalize.insiders import (
    CODE_LABELS,
    FiledTrade,
    LineKey,
    TradeGroup,
    group_trades,
)
from marketdata.sec import urls
from marketdata.service.companies import group_of, require, resolve_ticker
from marketdata.service.errors import InvalidParameter
from marketdata.service.pages import (
    DEFAULT_LIMIT,
    check_limit,
    decode_cursor,
    encode_cursor,
)
from marketdata.service.served import FlagRef, Number, Served, Source, newest

DEFAULT_DAYS = 90
MAX_DAYS = 730  # the backfill's two years
DEFAULT_CODES = ("P", "S")
_CODE = re.compile(r"^[A-Z]$")


class InsiderProof(BaseModel):
    """Where a trade comes from (§9.10): the Form 4 or 4/A, its XML and the lines read."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str  # 4 or 4/A
    filed_at: date
    filing_url: str  # the filing's folder on the SEC's site
    document_url: str  # the XML the lines were read from
    lines: tuple[int, ...]  # the line numbers in the filing, table I first
    method: str  # form4_xml
    extractor_version: str


class ServedLine(BaseModel):
    """A line as it is served now, with its proof."""

    model_config = ConfigDict(frozen=True)

    line: StoredLine
    proof: InsiderProof


def proof_of(filing: StoredFiling, lines: Sequence[int], extractor_version: str) -> InsiderProof:
    return InsiderProof(
        accession_no=filing.accession_no,
        form=filing.form,
        filed_at=filing.filed_at,
        filing_url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
        document_url=urls.filing_document(filing.issuer_cik, filing.accession_no, filing.doc_name),
        lines=tuple(lines),
        method=filing.method,
        extractor_version=extractor_version,
    )


async def served_now(
    conn: AsyncConnection[Any], keys: Sequence[LineKey]
) -> dict[LineKey, ServedLine | None]:
    """What each of these lines serves now: itself, the line that supersedes it (following a
    later 4/A), or nothing (None) when a 4/A restated its filing without it."""
    found: dict[LineKey, ServedLine | None] = {}
    wanted = set(keys)
    stored: dict[LineKey, StoredLine] = {}
    for _ in range(8):  # a chain of amendments is short; a cycle would be a bug
        missing = sorted({key[0] for key in wanted if key not in stored})
        if not missing:
            break
        for line in await insiders.lines(conn, missing):
            stored[(line.accession_no, line.line_no)] = line
        wanted = {
            (line.superseded_by, line.superseded_line)
            for line in stored.values()
            if line.superseded_by is not None and line.superseded_line is not None
        } - set(stored)
    filings = {
        filing.accession_no: filing
        for filing in await insiders.filings(conn, sorted({key[0] for key in stored}))
    }
    for key in keys:
        serving = _serving(stored, key)
        filing = filings.get(serving.accession_no) if serving is not None else None
        found[key] = (
            ServedLine(
                line=serving,
                proof=proof_of(filing, [serving.line_no], serving.extractor_version),
            )
            if serving is not None and filing is not None
            else None
        )
    return found


def _serving(stored: dict[LineKey, StoredLine], key: LineKey) -> StoredLine | None:
    """The line that serves `key`'s trade now, following the lines that superseded it."""
    line = stored.get(key)
    for _ in range(8):
        if line is None or line.superseded_by is None:
            return line
        if line.superseded_line is None or line.superseded_reason == "duplicate":
            return None
        line = stored.get((line.superseded_by, line.superseded_line))
    return None


async def filed_in(
    conn: AsyncConnection[Any], ciks: Sequence[int], first: date, last: date
) -> dict[str, OurFiling]:
    """The Forms 4 about these issuers filed from `first` to `last` as we parsed them, every
    line as filed, superseded or not (E8-S4: compared with the SEC's data set)."""
    stored = await insiders.filed_between(conn, ciks, first, last)
    lines: dict[str, list[StoredLine]] = {filing.accession_no: [] for filing in stored}
    for line in await insiders.lines(conn, list(lines)):
        lines[line.accession_no].append(line)
    return {
        filing.accession_no: OurFiling(
            filing.accession_no,
            filing.issuer_cik,
            filing.form,
            filing.filed_at,
            filing.rule_10b5_1,
            lines[filing.accession_no],
        )
        for filing in stored
    }


class Insider(BaseModel):
    """A reporting owner and its relationship to the issuer, as its filing states it."""

    model_config = ConfigDict(frozen=True)

    cik: int
    name: str
    roles: tuple[str, ...]  # director, officer, ten_percent_owner, other
    officer_title: str | None
    other_text: str | None  # what `other` means, e.g. "Member of 13(d) group"


class TradeProof(BaseModel):
    """Where a trade comes from (§9.10): each filing it sums, with the lines read from it."""

    model_config = ConfigDict(frozen=True)

    filings: tuple[InsiderProof, ...]
    checks: tuple[str, ...]  # issuer_matches_listing, direction_matches_code, price_reported


class InsiderTrade(BaseModel):
    """One insider group's trades of one security on one day with one code (§9.7): shares
    summed, the price weighted by shares, the value as shares times price."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    insiders: tuple[Insider, ...]  # joint filers are one group
    code: str  # P, S, A, M, F, G ... as filed
    code_label: str | None
    security: str | None  # as filed, e.g. Class A Common Stock
    derivative: bool  # table II: options, units, warrants
    acquired_disposed: str | None  # A or D; null when the lines differ
    shares: Number | None  # summed; units of the security
    average_price: Number | None  # USD per share, weighted by shares, rounded to 4 decimals
    value: Number | None  # USD, shares times price over the lines with a price
    unit: str = "USD"  # of the price and the value; shares are units of the security
    holdings_after: Number | None  # the last line's; null when the lines' ownership differs
    direct_indirect: str | None  # D (direct) or I (indirect, e.g. by a trust)
    rule_10b5_1: bool | None  # the filing's 10b5-1 box: a trade in it was made under a plan
    footnotes: tuple[str, ...]  # the lines' footnotes: weighted-average ranges, plans, trusts
    quality_flags: tuple[str, ...]
    proof: TradeProof


class CodeTotal(BaseModel):
    """The window's trades with one code."""

    model_config = ConfigDict(frozen=True)

    code: str
    code_label: str | None
    trades: int  # grouped trades, as listed
    total_shares: Number | None
    total_value: Number | None  # USD, over the trades with a price


class InsiderSummary(BaseModel):
    """One insider group's trades with one code over the window."""

    model_config = ConfigDict(frozen=True)

    insiders: tuple[Insider, ...]
    code: str
    trades: int
    total_shares: Number | None
    total_value: Number | None  # USD, over the trades with a price
    first_date: date
    last_date: date
    rule_10b5_1: bool | None  # the box of every filing, when they agree


class InsiderActivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    since: date  # trades on or after this day
    until: date  # and on or before this one (today, UTC)
    codes: tuple[str, ...] | None  # null: every code
    include_derivative: bool
    totals: tuple[CodeTotal, ...]  # by code
    insiders: tuple[InsiderSummary, ...]  # by latest trade, newest first (never by size)
    trades: tuple[InsiderTrade, ...]  # newest first, a page
    next_cursor: str | None


def check_codes(codes: Sequence[str] | None) -> tuple[str, ...] | None:
    """Transaction codes asked for: P and S by default, `all` for every code."""
    if codes is None:
        return DEFAULT_CODES
    wanted = tuple(dict.fromkeys(code.strip().upper() for code in codes if code.strip()))
    if wanted == ("ALL",):
        return None
    bad = [code for code in wanted if not _CODE.match(code)]
    if bad or not wanted:
        raise InvalidParameter(
            "codes are Form 4 transaction codes, one letter each (P purchase, S sale, A award, "
            f"M exercise, F tax withholding, G gift ...), or `all`; got {', '.join(bad) or 'none'}"
        )
    return wanted


def check_days(days: int, maximum: int = MAX_DAYS) -> None:
    if not 1 <= days <= maximum:
        raise InvalidParameter(f"days must be from 1 to {maximum}, not {days}")


async def insider_activity(
    conn: AsyncConnection[Any],
    ticker: str,
    *,
    days: int = DEFAULT_DAYS,
    codes: Sequence[str] | None = None,
    include_derivative: bool = False,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    today: date | None = None,
    max_days: int = MAX_DAYS,
) -> Served[InsiderActivity]:
    """A company's insider trades of the last `days` days (its predecessors' too)."""
    check_limit(limit)
    check_days(days, max_days)
    wanted = check_codes(codes)
    security = await resolve_ticker(conn, ticker)
    require(security, "insiders")
    until = today or datetime.now(UTC).date()
    since = until - timedelta(days=days)
    ciks = await group_of(conn, security.cik)
    lines = await insiders.served_lines(
        conn, ciks, since, until, codes=wanted, derivative=include_derivative
    )
    accessions = sorted({line.accession_no for line in lines})
    filings = {filing.accession_no: filing for filing in await insiders.filings(conn, accessions)}
    owners: dict[str, list[StoredOwner]] = {}
    for owner in await insiders.owners(conn, accessions):
        owners.setdefault(owner.accession_no, []).append(owner)
    groups = group_trades(
        [
            FiledTrade(
                line.accession_no,
                tuple(filings[line.accession_no].owner_ciks),
                filings[line.accession_no].rule_10b5_1,
                line,
            )
            for line in lines
        ]
    )
    trades = [_trade(group, filings, owners) for group in groups]
    keys = [_sort_key(trade) for trade in trades]
    start = 0
    if cursor:
        after = tuple(decode_cursor(cursor, 4))
        start = next((n for n, key in enumerate(keys) if key > after), len(keys))
    page = trades[start : start + limit]
    more = start + limit < len(trades)
    used = {proof.accession_no for trade in page for proof in trade.proof.filings}
    activity = InsiderActivity(
        ticker=security.ticker,
        cik=security.cik,
        company=security.name,
        since=since,
        until=until,
        codes=wanted,
        include_derivative=include_derivative,
        totals=_totals(trades),
        insiders=_summaries(trades),
        trades=tuple(page),
        next_cursor=encode_cursor(*keys[start + limit - 1]) if more else None,
    )
    return Served(
        data=activity,
        sources=tuple(
            Source(
                accession_no=filing.accession_no,
                form=filing.form,
                filed_at=filing.filed_at,
                url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
            )
            for filing in sorted(
                (filings[accession] for accession in used),
                key=lambda filing: (filing.filed_at, filing.accession_no),
                reverse=True,
            )
        ),
        as_of=newest(filing.parsed_at for filing in filings.values()),
        quality_flags=tuple(
            FlagRef(
                code=code,
                metric="insider_trade",
                period_end=trade.trade_date,
                ticker=security.ticker,
            )
            for trade in page
            for code in trade.quality_flags
        ),
    )


def _trade(
    group: TradeGroup,
    filings: dict[str, StoredFiling],
    owners: dict[str, list[StoredOwner]],
) -> InsiderTrade:
    by_filing: dict[str, list[int]] = {}
    for trade in group.trades:
        by_filing.setdefault(trade.accession_no, []).append(trade.line.line_no)
    latest = max(by_filing, key=lambda accession: (filings[accession].filed_at, accession))
    footnotes: dict[str, None] = {}
    for trade in group.trades:
        notes = filings[trade.accession_no].footnotes
        line = trade.line
        if isinstance(line, StoredLine):
            for ids in line.footnotes.values():
                for note in ids:
                    if note in notes:
                        footnotes[notes[note]] = None
    return InsiderTrade(
        trade_date=group.trade_date,
        insiders=tuple(_insider(owner) for owner in owners.get(latest, [])),
        code=group.code,
        code_label=CODE_LABELS.get(group.code),
        security=group.security_title,
        derivative=group.is_derivative,
        acquired_disposed=group.acquired_disposed,
        shares=group.shares,
        average_price=group.average_price,
        value=group.value,
        holdings_after=group.holdings_after,
        direct_indirect=group.direct_indirect,
        rule_10b5_1=group.rule_10b5_1,
        footnotes=tuple(footnotes),
        quality_flags=group.flags,
        proof=TradeProof(
            filings=tuple(
                proof_of(filings[accession], numbers, _version(group, accession))
                for accession, numbers in by_filing.items()
            ),
            checks=group.checks,
        ),
    )


def _version(group: TradeGroup, accession_no: str) -> str:
    for trade in group.trades:
        if trade.accession_no == accession_no and isinstance(trade.line, StoredLine):
            return trade.line.extractor_version
    return ""


def _insider(owner: StoredOwner) -> Insider:
    roles = [
        role
        for role, ticked in (
            ("director", owner.is_director),
            ("officer", owner.is_officer),
            ("ten_percent_owner", owner.is_ten_pct_owner),
            ("other", owner.is_other),
        )
        if ticked
    ]
    return Insider(
        cik=owner.owner_cik,
        name=owner.owner_name,
        roles=tuple(roles),
        officer_title=owner.officer_title,
        other_text=owner.other_text,
    )


def _sort_key(trade: InsiderTrade) -> tuple[str, str, str, str]:
    """Newest first, then by insider, code and security: a key that only grows down the list."""
    who = ",".join(str(insider.cik) for insider in trade.insiders)
    return (
        f"{99999999 - int(trade.trade_date.strftime('%Y%m%d')):08d}",
        who,
        trade.code,
        f"{int(trade.derivative)}{trade.security or ''}",
    )


def _totals(trades: Sequence[InsiderTrade]) -> tuple[CodeTotal, ...]:
    by_code: dict[str, list[InsiderTrade]] = {}
    for trade in trades:
        by_code.setdefault(trade.code, []).append(trade)
    return tuple(
        CodeTotal(
            code=code,
            code_label=CODE_LABELS.get(code),
            trades=len(listed),
            total_shares=_sum(trade.shares for trade in listed),
            total_value=_sum(trade.value for trade in listed),
        )
        for code, listed in sorted(by_code.items())
    )


def _summaries(trades: Sequence[InsiderTrade]) -> tuple[InsiderSummary, ...]:
    by_group: dict[tuple[tuple[int, ...], str], list[InsiderTrade]] = {}
    for trade in trades:
        who = tuple(insider.cik for insider in trade.insiders)
        by_group.setdefault((who, trade.code), []).append(trade)
    summaries = [
        InsiderSummary(
            insiders=listed[0].insiders,
            code=code,
            trades=len(listed),
            total_shares=_sum(trade.shares for trade in listed),
            total_value=_sum(trade.value for trade in listed),
            first_date=min(trade.trade_date for trade in listed),
            last_date=max(trade.trade_date for trade in listed),
            rule_10b5_1=_agreed(trade.rule_10b5_1 for trade in listed),
        )
        for (_, code), listed in by_group.items()
    ]
    return tuple(
        sorted(summaries, key=lambda summary: (summary.last_date, summary.code), reverse=True)
    )


def _sum(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    return sum(present, Decimal(0)) if present else None


def _agreed(values: Iterable[bool | None]) -> bool | None:
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None
