"""What a proof page shows (PLAN.md §9.10; E5b-S3): a value, its proof, the SEC links, the checks
it passed, the cross-check and its change history, read from clean tables with the API's own
functions, so a page and the API never disagree.

- A release (`/proof/earnings/{accession_no}`): the API's release object (D-097) with each
  slot's source quote, scale, checks or reasons and cross-check, and every change to its values,
  an 8-K/A's included.
- A metric value (`/proof/metrics/{ticker}/{metric}/{period_type}/{period_end}`): the row the
  API serves for that period (the company's, else a predecessor's, D-084), how it was obtained,
  the filings it reads and its change history.
"""

from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import financial_metrics, value_changes
from marketdata.db.repositories.value_changes import StoredChange
from marketdata.normalize.quality import DESCRIPTIONS
from marketdata.service.companies import group_of, require, resolve_ticker
from marketdata.service.earnings import EarningsRelease, get_earnings
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.financials import (
    MetricView,
    check_metrics,
    check_period,
    metric_refs,
    metric_view,
)
from marketdata.service.served import Number, Source, sources_of

SLOT_NAMES = {
    "revenue": "Revenue",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "eps_diluted": "Diluted EPS",
}
CHECKS = {
    "in_source": "the printed figure is in the filed document, in the row quoted",
    "scale": "the value is the printed figure times the stated scale, exactly",
    "sanity": (
        "the change from a year earlier is within ±1000%, revenue is positive, and diluted EPS "
        "agrees with net income and the diluted shares where the table prints them"
    ),
}
CROSS_CHECKS = {
    "matched": "equal to the later 10-Q or 10-K within 0.5% (EPS: within $0.01)",
    "mismatch": "differs from the later 10-Q or 10-K by more than 0.5% (EPS: more than $0.01)",
    "not_comparable": "the later 10-Q or 10-K has no comparable value",
    "pending": "the 10-Q or 10-K for this quarter isn't loaded yet",
}
METHODS = {
    "reported": "reported for this period in the filing",
    "derived_ytd": (
        "a year-to-date value less the shorter year-to-date value before it, both from the "
        "filings named"
    ),
    "derived_q4": "the fiscal year less its first nine months (or its first three quarters)",
    "derived_ttm": (
        "the sum of the last four quarters; for a balance, the value at the latest quarter end"
    ),
    "computed": "computed from other metrics: a difference, a margin or a change in percent",
}
REASONS = {
    "correction": "our rules, an override or the calc version changed",
    "restatement": "a later filing reports the period with another value",
    "amendment": "the company amended the release (8-K/A)",
}


class HistoryEntry(BaseModel):
    """One change to a value on the page."""

    model_config = ConfigDict(frozen=True)

    change_id: int
    changed_at: Any  # datetime; Any keeps the template's formatting free
    reason: str
    detail: str | None
    what: str  # e.g. "Revenue, value" or "revenue, Q 2026-06-27"
    old_value: Number | None
    new_value: Number | None


class EarningsProof(BaseModel):
    model_config = ConfigDict(frozen=True)

    release: EarningsRelease
    sources: tuple[Source, ...]
    history: tuple[HistoryEntry, ...]
    flags: dict[str, str]  # each flag on the page, with what it means


class MetricProofPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    row: MetricView
    sources: tuple[Source, ...]
    history: tuple[HistoryEntry, ...]
    flags: dict[str, str]


async def earnings_proof(conn: AsyncConnection[Any], accession_no: str) -> EarningsProof:
    """A release with its proof and every change to its values, whatever its status."""
    served = await get_earnings(conn, accession_no)
    release = served.data
    stored = await value_changes.for_release(conn, release.release.accession_no)
    codes = {code for fact in release.facts for code in fact.quality_flags}
    return EarningsProof(
        release=release,
        sources=served.sources,
        history=tuple(_entry(change) for change in stored),
        flags={code: DESCRIPTIONS.get(code, code) for code in sorted(codes)},
    )


async def metric_proof(
    conn: AsyncConnection[Any], ticker: str, metric: str, period_type: str, period_end: str
) -> MetricProofPage:
    """The value the API serves for this period of a company (or its predecessor)."""
    check_metrics([metric])
    period = check_period(period_type)
    try:
        end = date.fromisoformat(period_end)
    except ValueError:
        raise InvalidParameter(f"period_end must be a day like 2026-06-30, not {period_end!r}")
    security = await resolve_ticker(conn, ticker)
    require(security, "fundamentals")
    group = await group_of(conn, security.cik)
    rows = await financial_metrics.rows_at(conn, [(cik, metric, period, end) for cik in group])
    rank = {cik: position for position, cik in enumerate(group)}
    if not rows:
        raise NotFound(
            f"No {metric} value of {security.ticker} is stored for the {period} period ending "
            f"{end}."
        )
    row = metric_view(min(rows, key=lambda stored: rank[stored.cik]))
    history = [
        change
        for change in await value_changes.for_metric(conn, row.cik, metric)
        if change.entity_key.get("period_type") == period
        and change.entity_key.get("period_end") == end.isoformat()
    ]
    return MetricProofPage(
        ticker=security.ticker,
        cik=security.cik,
        company=security.name,
        row=row,
        sources=await sources_of(conn, metric_refs([row])),
        history=tuple(_entry(change) for change in history),
        flags={code: DESCRIPTIONS.get(code, code) for code in row.quality_flags},
    )


def _entry(change: StoredChange) -> HistoryEntry:
    key = change.entity_key
    if change.entity == "earnings_fact":
        slot = str(key.get("slot"))
        field = "the year before" if key.get("field") == "prior_value" else "value"
        what = f"{SLOT_NAMES.get(slot, slot)}, {field}"
        if key.get("amends"):
            what += f" (8-K/A {key.get('accession_no')})"
    else:
        what = f"{key.get('metric')}, {key.get('period_type')} {key.get('period_end')}"
    return HistoryEntry(
        change_id=change.change_id,
        changed_at=change.changed_at,
        reason=change.reason,
        detail=change.detail,
        what=what,
        old_value=change.old_value,
        new_value=change.new_value,
    )
