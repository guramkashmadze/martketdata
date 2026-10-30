"""The quality report (E4b-S3, S6; PLAN.md §9.5, §9.10): `marketdata quality report`.

Reads financial_metrics, earnings_releases and earnings_facts only (CLAUDE.md). It lists the
flags on each company's metric rows by metric and code, with how many rows carry each and the
latest period flagged, and the companies whose data is stale: their latest 10-Q or 10-K with
facts is more than 120 days old (§9.5 stale_data, decided on the day the report is read). A
predecessor's rows (D-068) are listed under its own CIK with the ticker of the company it
became; it is never stale on its own, since the company's filings carry on its history.

The release-vs-filing cross-check (D-028): slots by status, the match rate (matched over
matched and mismatched) and every mismatch, with both values and the filing.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import (
    companies,
    earnings,
    financial_metrics,
    predecessors,
    securities,
)
from marketdata.db.repositories.earnings import CrossChecked
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


class Mismatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int
    ticker: str | None
    accession_no: str  # the release
    period_end: date | None
    slot: str
    unit: str
    release_value: Decimal | None
    filing_value: Decimal | None
    filing_accession: str | None
    difference_pct: Decimal | None  # (release - filing) / |filing| x 100, to one decimal


class CrossChecks(BaseModel):
    model_config = ConfigDict(frozen=True)

    statuses: dict[str, int]  # slots by status: matched, mismatch, not_comparable, pending
    mismatches: tuple[Mismatch, ...]

    @property
    def match_rate(self) -> Decimal | None:
        """Matched over matched and mismatched slots, as a percentage to one decimal."""
        compared = self.statuses.get("matched", 0) + self.statuses.get("mismatch", 0)
        if not compared:
            return None
        rate = Decimal(100 * self.statuses.get("matched", 0)) / compared
        return rate.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    today: date
    rows: int
    flagged: int
    by_flag: dict[str, int]  # rows carrying each flag, most first
    cross_checks: CrossChecks
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
    checked = await earnings.cross_checks(conn, ciks)
    listed = sorted({*rows, *(slot.cik for slot in checked)})
    successors = await predecessors.successors(conn, listed)
    labelled = sorted({*listed, *successors.values()})
    names = {company.cik: company.name for company in await companies.get_many(conn, labelled)}
    tickers = await _tickers(conn, labelled)
    return tally(
        counts,
        rows,
        today=today,
        names=names,
        tickers=tickers,
        successors=successors,
        flags=flags,
        checked=checked,
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
    checked: Sequence[CrossChecked] = (),
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
        cross_checks=summarize(checked, tickers, successors),
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


def summarize(
    checked: Sequence[CrossChecked], tickers: Mapping[int, str], successors: Mapping[int, int]
) -> CrossChecks:
    """The cross-check's slots by status and every mismatch, with both values (D-028)."""
    statuses = Counter(slot.cross_check_status for slot in checked)
    mismatches = [
        Mismatch(
            cik=slot.cik,
            ticker=tickers.get(slot.cik) or tickers.get(successors.get(slot.cik, 0)),
            accession_no=slot.accession_no,
            period_end=slot.period_end,
            slot=slot.slot,
            unit=slot.unit,
            release_value=slot.value,
            filing_value=slot.cross_check_value,
            filing_accession=slot.cross_check_accession,
            difference_pct=_difference(slot.value, slot.cross_check_value),
        )
        for slot in checked
        if slot.cross_check_status == "mismatch"
    ]
    return CrossChecks(
        statuses={
            status: statuses[status]
            for status in ("matched", "mismatch", "not_comparable", "pending")
        },
        mismatches=tuple(sorted(mismatches, key=lambda m: (m.ticker or "~", m.accession_no))),
    )


async def cross_check_summary(conn: AsyncConnection[Any]) -> CrossChecks:
    """Every release's cross-check, for the accuracy report (E5b-S4)."""
    checked = await earnings.cross_checks(conn, None)
    ciks = sorted({slot.cik for slot in checked})
    successors = await predecessors.successors(conn, ciks)
    return summarize(checked, await _tickers(conn, [*ciks, *successors.values()]), successors)


async def _tickers(conn: AsyncConnection[Any], ciks: Sequence[int]) -> dict[int, str]:
    """Each company's ticker for a report: its common stock's first."""
    tickers: dict[int, str] = {}
    for security in sorted(
        await securities.list_current_for_ciks(conn, sorted(set(ciks))),
        key=lambda row: (row.security_type != "common", row.ticker),
    ):
        tickers.setdefault(security.cik, security.ticker)
    return tickers


def _difference(release: Decimal | None, filing: Decimal | None) -> Decimal | None:
    if release is None or filing is None or filing == 0:
        return None
    change = (release - filing) / abs(filing) * 100
    return change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
