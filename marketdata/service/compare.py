"""Side-by-side comparison of 2-5 companies (E5a, PLAN.md §10.2).

For each metric, each company's newest value of the period type asked for (by default the
trailing twelve months, which compares companies with different fiscal years; D-098), each
with its own period and proof. A company none of whose filings reports a metric has null
there. A comparison shows values side by side; it never ranks or scores them (D-029).
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.metric_catalog import UNITS
from marketdata.service.companies import CompanyRef, require, resolve_ticker
from marketdata.service.errors import InvalidParameter
from marketdata.service.financials import (
    CORE_METRICS,
    MetricView,
    Period,
    check_metrics,
    check_period,
    metric_flags,
    metric_refs,
    metric_rows,
)
from marketdata.service.served import Served, newest, sources_of, unique_flags

MIN_COMPANIES, MAX_COMPANIES = 2, 5


class ComparedMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    unit: str
    # By ticker, in the order asked: the company's newest value, or null when none of its
    # filings reports the metric.
    values: dict[str, MetricView | None]


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_type: Period
    companies: tuple[CompanyRef, ...]
    metrics: tuple[ComparedMetric, ...]


async def compare(
    conn: AsyncConnection[Any],
    tickers: Sequence[str],
    *,
    metrics: Sequence[str] | None = None,
    period: str = "TTM",
    today: date | None = None,
) -> Served[Comparison]:
    if not MIN_COMPANIES <= len(tickers) <= MAX_COMPANIES:
        raise InvalidParameter(
            f"compare takes {MIN_COMPANIES} to {MAX_COMPANIES} tickers, got {len(tickers)}"
        )
    wanted = tuple(dict.fromkeys(metrics)) if metrics else CORE_METRICS
    check_metrics(wanted)
    period_type = check_period(period)
    resolved = [await resolve_ticker(conn, ticker) for ticker in tickers]
    seen: dict[int, str] = {}
    for security in resolved:
        require(security, "fundamentals")
        if security.cik in seen:
            raise InvalidParameter(
                f"{seen[security.cik]} and {security.ticker} are the same company; name it once"
            )
        seen[security.cik] = security.ticker
    found = {
        security.ticker: {
            each.metric: each
            for each in await metric_rows(conn, security.cik, wanted, period, limit=1, today=today)
        }
        for security in resolved
    }
    newest_rows = {
        ticker: [each.rows[0] for each in by_metric.values() if each.rows]
        for ticker, by_metric in found.items()
    }
    rows = [row for listed in newest_rows.values() for row in listed]
    return Served(
        data=Comparison(
            period_type=period_type,
            companies=tuple(
                CompanyRef(ticker=security.ticker, cik=security.cik, name=security.name)
                for security in resolved
            ),
            metrics=tuple(
                ComparedMetric(
                    metric=metric,
                    unit=UNITS[metric],
                    values={
                        ticker: (by_metric[metric].rows[0] if by_metric[metric].rows else None)
                        for ticker, by_metric in found.items()
                    },
                )
                for metric in wanted
            ),
        ),
        sources=await sources_of(conn, metric_refs(rows)),
        as_of=newest(
            each.updated_at for by_metric in found.values() for each in by_metric.values()
        ),
        quality_flags=unique_flags(
            flag for ticker, listed in newest_rows.items() for flag in metric_flags(listed, ticker)
        ),
    )
