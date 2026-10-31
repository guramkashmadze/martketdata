"""Value changes (D-027, PLAN.md §9.10; E5b-S2): `GET /v1/changes` and the `data.changed`
webhook event serve the same object.

A stored earnings fact or metric value never changes silently: value_changes logs the old value,
the new value and why, `correction` (our rules, an override or the calc version changed),
`restatement` (a later filing reports the period otherwise) or `amendment` (the company's 8-K/A
changed a release's values). Each change is served with its company, what changed (a release's
slot, or a metric's period), the reason and detail, and the value as it is served now with its
proof (§9.10), since a value may have changed again later. Newest first, a page at a time.

E8: an insider trade changes when a 4/A corrects or drops a Form 4's line, when a filing made
twice is served once, or when a new parser version reads the line otherwise. Its change names
the filing and line, the shares before and after (the rest is in the detail), the filing that
serves the trade now and the trade as served now with its proof.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import financial_metrics, value_changes
from marketdata.db.repositories.value_changes import StoredChange
from marketdata.service.companies import CompanyRef, company_refs, group_of, resolve_ticker
from marketdata.service.earnings import EarningsFact, EarningsRelease, FactProof, get_earnings
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.financials import MetricProof, MetricView, metric_refs, metric_view
from marketdata.service.insiders import InsiderProof, ServedLine, served_now
from marketdata.service.pages import (
    BAD_CURSOR,
    DEFAULT_LIMIT,
    check_limit,
    decode_cursor,
    encode_cursor,
    start_of,
)
from marketdata.service.served import (
    FlagRef,
    Number,
    Served,
    Source,
    merge_sources,
    newest,
    sources_of,
    unique_flags,
)

type Reason = Literal["correction", "restatement", "amendment"]
REASONS: tuple[Reason, ...] = ("correction", "restatement", "amendment")
GONE = "the value is no longer stored"
NOT_SERVED = "the trade is no longer served: the detail says why"


class ValueChange(BaseModel):
    """One change to a served value, with the value as it is served now."""

    model_config = ConfigDict(frozen=True)

    change_id: int  # increases with every change: a later change has a larger id
    changed_at: datetime
    reason: Reason
    detail: str | None  # e.g. the rules versions, or the filing that restates
    entity: Literal["earnings_fact", "financial_metric", "insider_transaction"]
    ticker: str | None
    cik: int  # the company's, also for a value its predecessor filed
    company: str | None
    filer_cik: int  # whose value changed: the company, or a predecessor
    accession_no: str | None  # an earnings fact: the release (8-K or 8-K/A) it belongs to
    amends: str | None  # an 8-K/A: the release whose values it changed
    slot: str | None  # an earnings fact: revenue, operating_income, net_income, eps_diluted
    line_no: int | None = None  # an insider trade: its line in the Form 4 (`accession_no`)
    served_from: str | None = None  # an insider trade: the filing that serves it now
    field: str  # value, or prior_value: the same quarter a year earlier; shares for a trade
    metric: str | None  # a metric value
    period_type: str | None  # a metric value: Q, FY or TTM
    period_end: date | None
    old_value: Number | None
    new_value: Number | None
    unit: str | None
    value: Number | None  # what is served now, with the proof below
    reasons: tuple[str, ...]  # why the value served now is null
    quality_flags: tuple[str, ...]
    proof: FactProof | MetricProof | InsiderProof | None


class ChangeFeed(BaseModel):
    model_config = ConfigDict(frozen=True)

    changes: tuple[ValueChange, ...]  # newest first
    next_cursor: str | None  # pass it as `cursor` for the next page; null on the last


def check_reason(reason: str | None) -> Reason | None:
    if reason is None:
        return None
    for known in REASONS:
        if reason == known:
            return known
    raise InvalidParameter(f"reason must be one of {', '.join(REASONS)}, not {reason!r}")


async def changes_feed(
    conn: AsyncConnection[Any],
    *,
    since: date | datetime | None = None,
    ticker: str | None = None,
    reason: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Served[ChangeFeed]:
    """Changes to served values, newest first: of one company and its predecessors when
    `ticker` is given, made at or after `since` (a day means its start, UTC), for one reason."""
    check_limit(limit)
    wanted = check_reason(reason)
    before = None
    if cursor:
        (last,) = decode_cursor(cursor, 1)
        if not last.isdigit():
            raise InvalidParameter(BAD_CURSOR)
        before = int(last)
    ciks = None
    known: dict[int, CompanyRef] = {}
    if ticker is not None:
        security = await resolve_ticker(conn, ticker)
        ciks = await group_of(conn, security.cik)
        known = dict.fromkeys(
            ciks, CompanyRef(ticker=security.ticker, cik=security.cik, name=security.name)
        )
    stored = await value_changes.page(
        conn, ciks, since=start_of(since), reason=wanted, before=before, limit=limit + 1
    )
    page = stored[:limit]
    built = await _build(conn, page, known)
    next_cursor = encode_cursor(str(page[-1].change_id)) if len(stored) > limit else None
    return Served(
        data=ChangeFeed(changes=built.changes, next_cursor=next_cursor),
        sources=built.sources,
        as_of=built.as_of,
        quality_flags=built.quality_flags,
    )


async def get_change(conn: AsyncConnection[Any], change_id: int) -> Served[ValueChange]:
    """One change by its id (the `data.changed` event's object)."""
    stored = await value_changes.by_id(conn, [change_id])
    if not stored:
        raise NotFound(f"No value change has id {change_id}.")
    built = await _build(conn, stored, {})
    return Served(
        data=built.changes[0],
        sources=built.sources,
        as_of=built.as_of,
        quality_flags=built.quality_flags,
    )


def proof_path(change: ValueChange) -> str | None:
    """The proof page of the changed value (§9.10)."""
    if change.entity == "earnings_fact" and change.accession_no:
        return f"/proof/earnings/{change.accession_no}"
    if change.metric and change.ticker and change.period_type and change.period_end:
        return (
            f"/proof/metrics/{change.ticker}/{change.metric}/{change.period_type}/"
            f"{change.period_end.isoformat()}"
        )
    return None


@dataclass(frozen=True)
class _Built:
    changes: tuple[ValueChange, ...]
    sources: tuple[Source, ...]
    as_of: datetime | None
    quality_flags: tuple[FlagRef, ...]


async def _build(
    conn: AsyncConnection[Any], stored: Sequence[StoredChange], known: Mapping[int, CompanyRef]
) -> _Built:
    companies = {**await company_refs(conn, {change.cik for change in stored}), **known}
    releases: dict[str, Served[EarningsRelease]] = {}
    for accession in dict.fromkeys(
        str(change.entity_key.get("accession_no"))
        for change in stored
        if change.entity == "earnings_fact"
    ):
        try:
            releases[accession] = await get_earnings(conn, accession)
        except NotFound, InvalidParameter:
            continue
    keys = [_metric_key(change) for change in stored if change.entity == "financial_metric"]
    rows = {
        (row.cik, row.metric, row.period_type, row.period_end): metric_view(row)
        for row in await financial_metrics.rows_at(conn, keys)
    }
    trades = await served_now(
        conn, [_line_key(change) for change in stored if change.entity == "insider_transaction"]
    )
    changes = tuple(
        _change(change, companies[change.cik], releases, rows, trades) for change in stored
    )
    views = list(rows.values())
    flags = [
        FlagRef(
            code=code,
            metric=change.slot or change.metric or "",
            period_end=change.period_end,
            ticker=change.ticker,
        )
        for change in changes
        for code in change.quality_flags
    ]
    return _Built(
        changes=changes,
        sources=merge_sources(
            *(served.sources for served in releases.values()),
            await sources_of(conn, metric_refs(views)),
            [
                Source(
                    accession_no=trade.proof.accession_no,
                    form=trade.proof.form,
                    filed_at=trade.proof.filed_at,
                    url=trade.proof.filing_url,
                )
                for trade in trades.values()
                if trade is not None
            ],
        ),
        as_of=newest(
            [
                *(change.changed_at for change in changes),
                *(served.as_of for served in releases.values()),
            ]
        ),
        quality_flags=unique_flags(flags),
    )


def _metric_key(change: StoredChange) -> tuple[int, str, str, date]:
    key = change.entity_key
    return (
        change.cik,
        str(key["metric"]),
        str(key["period_type"]),
        date.fromisoformat(str(key["period_end"])),
    )


def _line_key(change: StoredChange) -> tuple[str, int]:
    return (str(change.entity_key["accession_no"]), int(change.entity_key["line_no"]))


def _change(
    change: StoredChange,
    company: CompanyRef,
    releases: Mapping[str, Served[EarningsRelease]],
    rows: Mapping[tuple[int, str, str, date], MetricView],
    trades: Mapping[tuple[str, int], ServedLine | None],
) -> ValueChange:
    key = change.entity_key
    field = str(key.get("field", "value"))
    common: dict[str, Any] = {
        "change_id": change.change_id,
        "changed_at": change.changed_at,
        "reason": check_reason(change.reason),
        "detail": change.detail,
        "ticker": company.ticker,
        "cik": company.cik,
        "company": company.name,
        "filer_cik": change.cik,
        "field": field,
        "old_value": change.old_value,
        "new_value": change.new_value,
    }
    if change.entity == "earnings_fact":
        accession = str(key.get("accession_no"))
        slot = str(key.get("slot"))
        served = releases.get(accession)
        fact = _fact(served.data, slot) if served is not None else None
        current = (
            (fact.value if field == "value" else fact.prior_value) if fact is not None else None
        )
        return ValueChange(
            **common,
            entity="earnings_fact",
            accession_no=accession,
            amends=str(key["amends"]) if key.get("amends") else None,
            slot=slot,
            metric=None,
            period_type=None,
            period_end=served.data.period.period_end if served is not None else None,
            unit=fact.unit if fact is not None else None,
            value=current,
            reasons=fact.reasons if fact is not None else (GONE,),
            quality_flags=fact.quality_flags if fact is not None else (),
            proof=fact.proof if fact is not None else None,
        )
    if change.entity == "insider_transaction":
        accession, line_no = _line_key(change)
        trade = trades.get((accession, line_no))
        return ValueChange(
            **common,
            entity="insider_transaction",
            accession_no=accession,
            amends=None,
            slot=None,
            line_no=line_no,
            served_from=str(key["served_from"]) if key.get("served_from") else None,
            metric=None,
            period_type=None,
            period_end=trade.line.trade_date if trade is not None else None,
            unit="shares",
            value=trade.line.shares if trade is not None else None,
            reasons=() if trade is not None else (NOT_SERVED,),
            quality_flags=(),
            proof=trade.proof if trade is not None else None,
        )
    metric_key = _metric_key(change)
    view = rows.get(metric_key)
    return ValueChange(
        **common,
        entity="financial_metric",
        accession_no=None,
        amends=None,
        slot=None,
        metric=metric_key[1],
        period_type=metric_key[2],
        period_end=metric_key[3],
        unit=view.unit if view is not None else None,
        value=view.value if view is not None else None,
        reasons=() if view is not None else (GONE,),
        quality_flags=view.quality_flags if view is not None else (),
        proof=view.proof if view is not None else None,
    )


def _fact(release: EarningsRelease, slot: str) -> EarningsFact | None:
    return next((fact for fact in release.facts if fact.slot == slot), None)
