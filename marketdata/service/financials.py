"""Metric rows with proof: E4a's `marketdata metrics show`; the API serves them (E5a).

Reads financial_metrics only (CLAUDE.md). Rows stay under the CIK whose filings they come from;
a company's series merges its predecessors' rows by period, the company's own winning where
both report one (D-068, D-084): XOM's quarters before the second of 2026 are under Exxon Mobil
Corp (34088). Each value carries its proof (§9.10): the filing on sec.gov, the XBRL concept,
the method, the contributing filings and the calc version. A value that doesn't apply is null
with the flag that says why; the newest value of a company whose filings stopped more than 120
days ago says so (`stale_data`, decided when read).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import financial_metrics
from marketdata.db.repositories.financial_metrics import StoredRow
from marketdata.normalize.metric_catalog import COMPUTED, METRICS, UNITS
from marketdata.normalize.quality import STALE_DATA, is_stale
from marketdata.sec import urls
from marketdata.service.companies import group_of, resolve_ticker
from marketdata.service.errors import InvalidParameter
from marketdata.service.served import FlagRef, Served, newest, sources_of, unique_flags

type Period = Literal["Q", "FY", "TTM"]
PERIODS: tuple[Period, ...] = ("Q", "FY", "TTM")
# The API's default metrics: the core set (D-020), the catalog's then the computed ones.
CORE_METRICS: tuple[str, ...] = (
    *(metric.name for metric in METRICS if metric.core),
    *(metric.name for metric in COMPUTED if metric.core),
)
DEFAULT_PERIODS = 8  # two years of quarters
MAX_PERIODS = 40


class MetricProof(BaseModel):
    """Where a metric value came from (§9.10)."""

    model_config = ConfigDict(frozen=True)

    accession_no: str | None  # the latest filing it reads
    filing_url: str | None  # that filing's index page on sec.gov
    concept: str | None  # the XBRL concept; null for computed values
    method: str  # reported, derived_ytd, derived_q4, derived_ttm or computed
    source_accessions: tuple[str, ...]  # every filing it reads
    calc_version: str


class MetricView(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int  # the filer: the company, or a predecessor
    metric: str
    period_type: str
    fiscal_year: int
    fiscal_period: str
    period_start: date | None
    period_end: date
    value: Decimal | None
    unit: str  # USD, shares, USD/shares or percent
    filed_at: date | None
    quality_flags: tuple[str, ...]
    proof: MetricProof


class Series(BaseModel):
    """Rows of one metric, newest first, and when the newest of them was last written."""

    model_config = ConfigDict(frozen=True)

    metric: str
    rows: tuple[MetricView, ...]
    updated_at: datetime | None


def check_metrics(metrics: Sequence[str]) -> None:
    unknown = [metric for metric in metrics if metric not in UNITS]
    if unknown:
        raise InvalidParameter(
            f"unknown metric {', '.join(repr(metric) for metric in unknown)}; the catalog has "
            f"{', '.join(sorted(UNITS))}"
        )


def check_period(period: str) -> Period:
    for known in PERIODS:
        if period == known:
            return known
    raise InvalidParameter(f"period must be Q, FY or TTM, not {period!r}")


async def metric_rows(
    conn: AsyncConnection[Any],
    cik: int,
    metrics: Sequence[str],
    period: str,
    *,
    limit: int = DEFAULT_PERIODS,
    today: date | None = None,
) -> list[Series]:
    """The latest `limit` periods of each of these metrics (catalog or computed) for a company
    and its predecessors, newest first, in the order asked. When the latest 10-Q or 10-K with
    facts is more than 120 days old on `today` (default: today, UTC), each newest value carries
    `stale_data` (§9.5). Raises InvalidParameter for a metric the catalog doesn't have or a
    period other than Q, FY or TTM."""
    check_metrics(metrics)
    check_period(period)
    group = await group_of(conn, cik)
    rank = {member: position for position, member in enumerate(group)}
    merged: dict[str, dict[date, StoredRow]] = {metric: {} for metric in metrics}
    for row in await financial_metrics.series_of(conn, group, metrics, period):
        kept = merged[row.metric].get(row.period_end)
        if kept is None or rank[row.cik] < rank[kept.cik]:
            merged[row.metric][row.period_end] = row
    filed = [latest for _, _, latest in (await financial_metrics.row_counts(conn, group)).values()]
    latest = max((day for day in filed if day is not None), default=None)
    stale = is_stale(latest, today or datetime.now(UTC).date())
    result = []
    for metric in metrics:
        newest_rows = sorted(merged[metric].values(), key=lambda row: row.period_end, reverse=True)
        kept_rows = newest_rows[:limit]
        views = [_view(row) for row in kept_rows]
        if views and stale:
            views[0] = views[0].model_copy(
                update={"quality_flags": (*views[0].quality_flags, STALE_DATA)}
            )
        result.append(
            Series(
                metric=metric,
                rows=tuple(views),
                updated_at=newest(row.updated_at for row in kept_rows),
            )
        )
    return result


async def metric_series(
    conn: AsyncConnection[Any],
    cik: int,
    metric: str,
    period: str,
    *,
    limit: int = DEFAULT_PERIODS,
    today: date | None = None,
) -> list[MetricView]:
    """The latest `limit` periods of one metric for a company and its predecessors, newest
    first (`metric_rows` for one metric)."""
    (series,) = await metric_rows(conn, cik, [metric], period, limit=limit, today=today)
    return list(series.rows)


class Financials(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    period_type: Period
    metrics: tuple[str, ...]  # the metrics asked for, in order
    rows: tuple[MetricView, ...]  # by metric as asked, each newest period first


async def get_financials(
    conn: AsyncConnection[Any],
    ticker: str,
    *,
    metrics: Sequence[str] | None = None,
    period: str = "Q",
    limit: int = DEFAULT_PERIODS,
    today: date | None = None,
) -> Served[Financials]:
    """A company's metric rows with proof (PLAN.md §10.2): the latest `limit` periods (1-40) of
    each metric, quarters (Q), fiscal years (FY) or trailing twelve months (TTM); by default
    the core set (D-020)."""
    wanted = tuple(dict.fromkeys(metrics)) if metrics else CORE_METRICS
    check_metrics(wanted)
    period_type = check_period(period)
    if not 1 <= limit <= MAX_PERIODS:
        raise InvalidParameter(f"limit must be from 1 to {MAX_PERIODS} periods, got {limit}")
    security = await resolve_ticker(conn, ticker)
    series = await metric_rows(conn, security.cik, wanted, period, limit=limit, today=today)
    rows = tuple(row for each in series for row in each.rows)
    return Served(
        data=Financials(
            ticker=security.ticker,
            cik=security.cik,
            company=security.name,
            period_type=period_type,
            metrics=wanted,
            rows=rows,
        ),
        sources=await sources_of(conn, metric_refs(rows)),
        as_of=newest(each.updated_at for each in series),
        quality_flags=metric_flags(rows, security.ticker),
    )


def metric_refs(rows: Sequence[MetricView]) -> list[tuple[int, str]]:
    """The filings these rows read, each with the filer's CIK."""
    return [(row.cik, accession) for row in rows for accession in row.proof.source_accessions]


def metric_flags(rows: Sequence[MetricView], ticker: str | None) -> tuple[FlagRef, ...]:
    return unique_flags(
        FlagRef(code=code, metric=row.metric, period_end=row.period_end, ticker=ticker)
        for row in rows
        for code in row.quality_flags
    )


def _view(row: StoredRow) -> MetricView:
    url = urls.filing_index_page(row.cik, row.accession_no) if row.accession_no else None
    return MetricView(
        cik=row.cik,
        metric=row.metric,
        period_type=row.period_type,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        period_start=row.period_start,
        period_end=row.period_end,
        value=row.value,
        unit=row.unit,
        filed_at=row.filed_at,
        quality_flags=tuple(row.quality_flags),
        proof=MetricProof(
            accession_no=row.accession_no,
            filing_url=url,
            concept=row.concept,
            method=row.method,
            source_accessions=tuple(row.source_accessions),
            calc_version=row.calc_version,
        ),
    )
