"""Metric rows with proof: E4a's `marketdata metrics show`; E5a serves them.

Reads financial_metrics only (CLAUDE.md). Rows stay under the CIK whose filings they come from;
a company's series merges its predecessors' rows by period, the company's own winning where
both report one (D-068, D-084): XOM's quarters before the second of 2026 are under Exxon Mobil
Corp (34088). Each value carries its proof (§9.10): the filing on sec.gov, the XBRL concept,
the method, the contributing filings and the calc version. A value that doesn't apply is null
with the flag that says why; the newest value of a company whose filings stopped more than 120
days ago says so (`stale_data`, decided when read).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import financial_metrics, predecessors
from marketdata.db.repositories.financial_metrics import StoredRow
from marketdata.normalize.metric_catalog import UNITS
from marketdata.normalize.quality import STALE_DATA, is_stale
from marketdata.sec import urls
from marketdata.service.errors import InvalidParameter

type Period = Literal["Q", "FY", "TTM"]
PERIODS: tuple[Period, ...] = ("Q", "FY", "TTM")


class MetricProof(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str | None
    filing_url: str | None  # the filing's index page on sec.gov
    concept: str | None
    method: str
    source_accessions: tuple[str, ...]
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
    unit: str
    filed_at: date | None
    quality_flags: tuple[str, ...]
    proof: MetricProof


async def metric_series(
    conn: AsyncConnection[Any],
    cik: int,
    metric: str,
    period: str,
    *,
    limit: int = 8,
    today: date | None = None,
) -> list[MetricView]:
    """The latest `limit` periods of one metric (catalog or computed) for a company and its
    predecessors, newest first. When the latest 10-Q or 10-K with facts is more than 120 days
    old on `today` (default: today, UTC), the newest value carries `stale_data` (§9.5).
    Raises InvalidParameter for a metric the catalog doesn't have or a period other than Q,
    FY or TTM."""
    if metric not in UNITS:
        raise InvalidParameter(
            f"unknown metric {metric!r}; the catalog has {', '.join(sorted(UNITS))}"
        )
    if period not in PERIODS:
        raise InvalidParameter(f"period must be Q, FY or TTM, not {period!r}")
    group = [cik, *await predecessors.predecessors_of(conn, cik)]
    rank = {member: position for position, member in enumerate(group)}
    merged: dict[date, StoredRow] = {}
    for row in await financial_metrics.series(conn, group, metric, period):
        kept = merged.get(row.period_end)
        if kept is None or rank[row.cik] < rank[kept.cik]:
            merged[row.period_end] = row
    newest = [
        _view(row)
        for row in sorted(merged.values(), key=lambda row: row.period_end, reverse=True)[:limit]
    ]
    filed = [latest for _, _, latest in (await financial_metrics.row_counts(conn, group)).values()]
    latest = max((day for day in filed if day is not None), default=None)
    if newest and is_stale(latest, today or datetime.now(UTC).date()):
        newest[0] = newest[0].model_copy(
            update={"quality_flags": (*newest[0].quality_flags, STALE_DATA)}
        )
    return newest


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
