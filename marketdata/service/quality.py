"""The quality report (E4b-S3, PLAN.md §9.5): `marketdata quality report`.

Reads financial_metrics only (CLAUDE.md). It lists the flags on each company's metric rows by
metric and code, with how many rows carry each and the latest period flagged, and the
companies whose data is stale: their latest 10-Q or 10-K with facts is more than 120 days old
(§9.5 stale_data, decided on the day the report is read). A predecessor's rows (D-068) are
listed under its own CIK with the ticker of the company it became; it is never stale on its
own, since the company's filings carry on its history.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import companies, financial_metrics, predecessors, securities
from marketdata.normalize.quality import is_stale


class FlagCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    flag: str
    rows: int
    latest: date  # the latest period end flagged


class CompanyFlags(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int
    ticker: str | None  # a predecessor shows the ticker of the company it became
    name: str | None
    successor_cik: int | None
    rows: int
    flagged: int  # rows with at least one flag
    latest_filed: date | None  # the latest filing among the company's rows (its group's)
    stale: bool
    flags: tuple[FlagCount, ...]  # by metric, then flag


class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    today: date
    rows: int
    flagged: int
    by_flag: dict[str, int]  # rows carrying each flag, most first
    companies: tuple[CompanyFlags, ...]  # by ticker, the company before its predecessors

    @property
    def stale(self) -> tuple[CompanyFlags, ...]:
        return tuple(company for company in self.companies if company.stale)


async def quality_report(
    conn: AsyncConnection[Any],
    *,
    today: date,
    ciks: Sequence[int] | None = None,
    flags: Sequence[str] | None = None,
) -> QualityReport:
    """Flags on the metric rows of these companies (all with rows when None), only these flag
    codes when given."""
    if ciks is not None:
        group = set(ciks)
        for cik in ciks:
            group.update(await predecessors.predecessors_of(conn, cik))
        ciks = sorted(group)
    counts = await financial_metrics.flag_counts(conn, ciks)
    rows = await financial_metrics.row_counts(conn, ciks)
    listed = sorted(rows)
    successors = await predecessors.successors(conn, listed)
    labelled = sorted({*listed, *successors.values()})
    names = {company.cik: company.name for company in await companies.get_many(conn, labelled)}
    tickers: dict[int, str] = {}
    for security in sorted(
        await securities.list_current_for_ciks(conn, labelled),
        key=lambda row: (row.security_type != "common", row.ticker),
    ):
        tickers.setdefault(security.cik, security.ticker)
    return tally(
        counts,
        rows,
        today=today,
        names=names,
        tickers=tickers,
        successors=successors,
        flags=flags,
    )


def tally(
    counts: Sequence[tuple[int, str, str, int, date]],
    rows: Mapping[int, tuple[int, int, date | None]],
    *,
    today: date,
    names: Mapping[int, str | None],
    tickers: Mapping[int, str],
    successors: Mapping[int, int],
    flags: Sequence[str] | None = None,
) -> QualityReport:
    wanted = set(flags) if flags else None
    by_company: dict[int, list[FlagCount]] = {}
    by_flag: Counter[str] = Counter()
    for cik, metric, flag, count, latest in counts:
        if wanted is not None and flag not in wanted:
            continue
        by_company.setdefault(cik, []).append(
            FlagCount(metric=metric, flag=flag, rows=count, latest=latest)
        )
        by_flag[flag] += count
    # A group's latest filing: the company's and its predecessors' rows together.
    group_filed: dict[int, date] = {}
    for cik, (_, _, filed) in rows.items():
        root = successors.get(cik, cik)
        if filed is not None and filed > group_filed.get(root, date.min):
            group_filed[root] = filed
    listed = []
    for cik, (count, flagged, _) in rows.items():
        successor = successors.get(cik)
        filed = group_filed.get(successor or cik)
        listed.append(
            CompanyFlags(
                cik=cik,
                ticker=tickers.get(cik) or tickers.get(successor or 0),
                name=names.get(cik),
                successor_cik=successor,
                rows=count,
                flagged=flagged,
                latest_filed=filed,
                stale=successor is None and is_stale(filed, today),
                flags=tuple(by_company.get(cik, ())),
            )
        )
    return QualityReport(
        today=today,
        rows=sum(count for count, _, _ in rows.values()),
        flagged=sum(flagged for _, flagged, _ in rows.values()),
        by_flag=dict(by_flag.most_common()),
        companies=tuple(
            sorted(
                listed,
                key=lambda company: (
                    company.ticker or "~",
                    company.successor_cik is not None,
                    company.cik,
                ),
            )
        ),
    )
