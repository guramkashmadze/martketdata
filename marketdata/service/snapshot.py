"""A company snapshot (E5a, PLAN.md §10.2): identity and class, the latest period, trailing
twelve months of the core metrics, the latest earnings release and the three latest filings
of the forms that report results and events. Prices and price-based metrics come with E10.
"""

from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import securities
from marketdata.normalize.security_master import FilerType
from marketdata.service.companies import company_profile, group_of, require, resolve_ticker
from marketdata.service.earnings import EarningsRelease, latest_earnings
from marketdata.service.errors import NotFound
from marketdata.service.filings import SNAPSHOT_FORMS, FilingView, filing_view, filings_page
from marketdata.service.financials import (
    CORE_METRICS,
    MetricView,
    metric_flags,
    metric_refs,
    metric_rows,
)
from marketdata.service.served import (
    Served,
    merge_sources,
    newest,
    sources_of,
    unique_flags,
)

SNAPSHOT_FILINGS = 3


class CompanyIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str  # the ticker asked for, as the SEC writes it
    tickers: tuple[str, ...]  # every current ticker of the company
    cik: int
    name: str | None
    exchange: str | None
    filer_type: FilerType
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None  # 'MMDD'
    state_of_inc: str | None


class LatestPeriod(BaseModel):
    """The latest quarter the company's filings report metrics for."""

    model_config = ConfigDict(frozen=True)

    fiscal_year: int
    fiscal_period: str
    period_end: date


class CompanySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: CompanyIdentity
    latest_period: LatestPeriod | None
    ttm: tuple[MetricView, ...]  # the newest trailing-twelve-month value of each core metric
    not_reported: tuple[str, ...]  # core metrics none of the company's filings report
    latest_release: EarningsRelease | None
    recent_filings: tuple[FilingView, ...]


async def company_snapshot(
    conn: AsyncConnection[Any], ticker: str, *, today: date | None = None
) -> Served[CompanySnapshot]:
    security = await resolve_ticker(conn, ticker)
    require(security, "fundamentals")
    profile = await company_profile(conn, security.cik)
    series = await metric_rows(conn, security.cik, CORE_METRICS, "TTM", limit=1, today=today)
    ttm = tuple(each.rows[0] for each in series if each.rows)
    newest_row = max(ttm, key=lambda row: row.period_end, default=None)
    try:
        release = await latest_earnings(conn, security.ticker)
    except NotFound:
        release = None
    listed, _ = await filings_page(
        conn,
        await group_of(conn, security.cik),
        forms=SNAPSHOT_FORMS,
        limit=SNAPSHOT_FILINGS,
    )
    current = await securities.list_current_for_cik(conn, security.cik)
    sources = await sources_of(
        conn,
        [
            *metric_refs(ttm),
            *((row.cik, row.accession_no) for row in listed),
        ],
    )
    return Served(
        data=CompanySnapshot(
            company=CompanyIdentity(
                ticker=security.ticker,
                tickers=tuple(row.ticker for row in current),
                cik=security.cik,
                name=security.name,
                exchange=security.exchange,
                filer_type=security.filer_type,
                sic_code=profile.sic_code,
                sic_description=profile.sic_description,
                fiscal_year_end=profile.fiscal_year_end,
                state_of_inc=profile.state_of_inc,
            ),
            latest_period=(
                LatestPeriod(
                    fiscal_year=newest_row.fiscal_year,
                    fiscal_period=newest_row.fiscal_period,
                    period_end=newest_row.period_end,
                )
                if newest_row is not None
                else None
            ),
            ttm=ttm,
            not_reported=tuple(each.metric for each in series if not each.rows),
            latest_release=release.data if release is not None else None,
            recent_filings=tuple(filing_view(row) for row in listed),
        ),
        sources=merge_sources(sources, release.sources if release is not None else ()),
        as_of=newest(
            [
                *(each.updated_at for each in series),
                release.as_of if release is not None else None,
                *(row.discovered_at for row in listed),
            ]
        ),
        quality_flags=unique_flags(
            [
                *metric_flags(ttm, security.ticker),
                *(release.quality_flags if release is not None else ()),
            ]
        ),
    )
