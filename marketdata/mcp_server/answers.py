"""The MCP tools' answers (E6-S2, D-108): the API's service results in a compact shape, so a
call stays under about 8 KB.

An answer is the API's envelope without its list of flags (each value carries its own): data,
sources, as_of and the disclaimer. Inside `data` a metric value keeps a short proof: the filing
it came from (`accession_no`; for a derived, TTM or computed value the latest filing it reads),
the method and the XBRL concept, named once for a series whose values share it. Every filing a
value reads is in `sources`, with its SEC link; recent_filings' filings carry their own links,
so its sources stay empty. The full proof object (each value's contributing filings, the calc
version, the filing's index page) is the API's and the proof page's. latest_earnings answers
the API's release as it is, each fact with its full proof; a snapshot's release keeps each
fact's source quote and cross-check status.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer

from marketdata.normalize.metric_catalog import UNITS
from marketdata.service.companies import CompanyRef, Lookup
from marketdata.service.compare import Comparison
from marketdata.service.earnings import EarningsRelease
from marketdata.service.filings import CompanyFilings, FilingEventView, FilingView
from marketdata.service.financials import Financials, MetricView
from marketdata.service.served import Number, Served, Source
from marketdata.service.snapshot import CompanyIdentity, CompanySnapshot, LatestPeriod


class Answer[T](BaseModel):
    """A tool's answer: the API's envelope without its list of flags."""

    model_config = ConfigDict(frozen=True)

    data: T
    sources: tuple[Source, ...]  # the filings the values read, newest first
    as_of: datetime | None


def lookup(served: Served[Lookup]) -> Answer[Lookup]:
    return Answer(data=served.data, sources=served.sources, as_of=served.as_of)


class Value(BaseModel):
    """A metric value with its short proof."""

    model_config = ConfigDict(frozen=True)

    period_end: date
    fiscal: str  # FY2026 Q3, or FY2026 for a year; a TTM value's is the quarter it ends with
    value: Number | None  # null with the flag that says why
    method: str  # reported, derived_ytd, derived_q4, derived_ttm or computed
    concept: str | None  # the XBRL concept; null for a computed value
    accession_no: str | None  # the filing; the latest one read for a derived or computed value
    flags: tuple[str, ...]


class Series(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    unit: str  # USD, shares, USD/shares or percent
    concept: str | None  # the concept of every value; null when they differ or have none
    values: tuple[Value, ...]  # newest first

    @model_serializer(mode="wrap")
    def _concept_once(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """A value names its concept only when the series doesn't and it has one."""
        written: dict[str, Any] = handler(self)
        for each, value in zip(written["values"], self.values, strict=True):
            if self.concept is not None or value.concept is None:
                del each["concept"]
        return written


def fiscal_label(fiscal_year: int, fiscal_period: str) -> str:
    """FY2026 Q3, or FY2026 for a fiscal year (§9.4 labels)."""
    return f"FY{fiscal_year}" if fiscal_period == "FY" else f"FY{fiscal_year} {fiscal_period}"


def value_of(row: MetricView) -> Value:
    return Value(
        period_end=row.period_end,
        fiscal=fiscal_label(row.fiscal_year, row.fiscal_period),
        value=row.value,
        method=row.proof.method,
        concept=row.proof.concept,
        accession_no=row.proof.accession_no,
        flags=row.quality_flags,
    )


def series_of(metric: str, rows: list[MetricView]) -> Series:
    values = tuple(value_of(row) for row in rows)
    concepts = {value.concept for value in values}
    return Series(
        metric=metric,
        unit=UNITS[metric],
        concept=concepts.pop() if len(concepts) == 1 else None,
        values=values,
    )


class FinancialsAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    period_type: str  # Q, FY or TTM
    metrics: tuple[Series, ...]  # as asked


def financials(served: Served[Financials]) -> Answer[FinancialsAnswer]:
    data = served.data
    rows: dict[str, list[MetricView]] = {metric: [] for metric in data.metrics}
    for row in data.rows:
        rows[row.metric].append(row)
    return Answer(
        data=FinancialsAnswer(
            ticker=data.ticker,
            cik=data.cik,
            company=data.company,
            period_type=data.period_type,
            metrics=tuple(series_of(metric, listed) for metric, listed in rows.items()),
        ),
        sources=served.sources,
        as_of=served.as_of,
    )


class Filing(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str
    filed_at: date
    period_of_report: date | None
    url: str  # the filing's index page
    events: tuple[FilingEventView, ...]  # plain-language labels (§9.6)


def filing_of(view: FilingView) -> Filing:
    return Filing(
        accession_no=view.accession_no,
        form=view.form,
        filed_at=view.filed_at,
        period_of_report=view.period_of_report,
        url=view.url,
        events=view.events,
    )


class FilingsAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    form: str | None  # the form asked for; null: reports and stakes (D-110)
    since: date  # filed on or after
    filings: tuple[Filing, ...]  # newest first
    more: bool  # more filings than these match: ask for fewer days or one form


def filings(
    served: Served[CompanyFilings], *, form: str | None, since: date
) -> Answer[FilingsAnswer]:
    data = served.data
    return Answer(
        data=FilingsAnswer(
            ticker=data.ticker,
            cik=data.cik,
            company=data.company,
            form=form,
            since=since,
            filings=tuple(filing_of(view) for view in data.filings),
            more=data.next_cursor is not None,
        ),
        sources=(),  # each filing carries its own link
        as_of=served.as_of,
    )


class ReleaseFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: str  # revenue, operating_income, net_income or eps_diluted
    value: Number | None  # null unless it passed every check
    unit: str  # USD or USD/shares
    prior_value: Number | None  # the same quarter a year earlier
    change_pct: Number | None
    check_status: str  # passed, failed, not_found or not_applicable
    cross_check: str  # against the later 10-Q or 10-K: pending, matched, mismatch, not_comparable
    source_quote: str | None  # the row as printed in the press release
    reasons: tuple[str, ...]  # why a value is null
    flags: tuple[str, ...]


class ReleaseSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    accepted_at: datetime | None
    status: str  # complete, partial or failed
    fiscal_year: int | None
    fiscal_period: str | None
    period_end: date | None
    document_url: str | None  # the press release
    facts: tuple[ReleaseFact, ...]


def release_summary(release: EarningsRelease) -> ReleaseSummary:
    return ReleaseSummary(
        accession_no=release.release.accession_no,
        accepted_at=release.release.accepted_at,
        status=release.release.status,
        fiscal_year=release.period.fiscal_year,
        fiscal_period=release.period.fiscal_period,
        period_end=release.period.period_end,
        document_url=release.release.document_url,
        facts=tuple(
            ReleaseFact(
                slot=fact.slot,
                value=fact.value,
                unit=fact.unit,
                prior_value=fact.prior_value,
                change_pct=fact.change_pct,
                check_status=fact.check_status,
                cross_check=fact.proof.cross_check.status,
                source_quote=fact.proof.source_quote,
                reasons=fact.reasons,
                flags=fact.quality_flags,
            )
            for fact in release.facts
        ),
    )


class SnapshotAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: CompanyIdentity
    latest_period: LatestPeriod | None
    ttm: tuple[Series, ...]  # the newest trailing-twelve-month value of each core metric
    not_reported: tuple[str, ...]  # core metrics none of the company's filings report
    latest_release: ReleaseSummary | None
    recent_filings: tuple[Filing, ...]


def snapshot(served: Served[CompanySnapshot]) -> Answer[SnapshotAnswer]:
    data = served.data
    return Answer(
        data=SnapshotAnswer(
            company=data.company,
            latest_period=data.latest_period,
            ttm=tuple(series_of(row.metric, [row]) for row in data.ttm),
            not_reported=data.not_reported,
            latest_release=(
                release_summary(data.latest_release) if data.latest_release is not None else None
            ),
            recent_filings=tuple(filing_of(view) for view in data.recent_filings),
        ),
        sources=served.sources,
        as_of=served.as_of,
    )


class ComparedValue(BaseModel):
    """A company's value in a comparison: its own period end and short proof."""

    model_config = ConfigDict(frozen=True)

    period_end: date
    value: Number | None
    method: str
    concept: str | None
    accession_no: str | None
    flags: tuple[str, ...]


class Compared(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    unit: str
    values: dict[str, ComparedValue | None]  # by ticker as asked; null when no filing reports it


class ComparisonAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_type: str
    companies: tuple[CompanyRef, ...]
    metrics: tuple[Compared, ...]


def compared_value(row: MetricView) -> ComparedValue:
    return ComparedValue(
        period_end=row.period_end,
        value=row.value,
        method=row.proof.method,
        concept=row.proof.concept,
        accession_no=row.proof.accession_no,
        flags=row.quality_flags,
    )


def comparison(served: Served[Comparison]) -> Answer[ComparisonAnswer]:
    data = served.data
    return Answer(
        data=ComparisonAnswer(
            period_type=data.period_type,
            companies=data.companies,
            metrics=tuple(
                Compared(
                    metric=compared.metric,
                    unit=compared.unit,
                    values={
                        ticker: compared_value(row) if row is not None else None
                        for ticker, row in compared.values.items()
                    },
                )
                for compared in data.metrics
            ),
        ),
        sources=served.sources,
        as_of=served.as_of,
    )
