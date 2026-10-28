"""Reported metric rows from XBRL facts (E4a-S4); E4b derives the rest.

Pure. For one company, from its stored facts (normalize/companyfacts.py), the metric catalog
(normalize/metric_catalog.py) and the period rules (normalize/periods.py):

1. Only facts of the 10-K and 10-Q family count, and of each concept, unit and period only the
   one filed last (§9.4 rule 2; D-083).
2. Flows: a row for every three-month period (period type Q) and every fiscal year (FY) that a
   catalog concept reports, from the first concept in the metric's order with a fact for that
   period (rule 3). Year-to-date periods wait for E4b's differencing. Two periods ending on
   the same day (a stub) give the one filed last.
3. Instants: a row at every quarter end (Q1-Q4) and every fiscal year end (FY, the same value
   as Q4). A quarter end is the end of any period the company reports. The cover-page share
   count sits at the end of the period its filing reports (§9.3's note).
4. Banks (SIC 6000-6099) have no revenue or gross profit, as in their releases (D-072): null
   with `not_applicable_financials` for each period of their other flows (rule 10, D-084).
5. Fiscal labels come from the company's own year ends (rule 7).

Every row is method `reported`, with its concept, the filing (accession_no, filed_at) and
CALC_VERSION: the catalog version, then the version of these rules.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Literal

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.metric_catalog import CATALOG_VERSION, METRICS, Concept, Metric
from marketdata.normalize.periods import (
    METRIC_FORMS,
    FiscalCalendar,
    classify,
    latest_facts,
)

RULES_VERSION = "1"
CALC_VERSION = f"{CATALOG_VERSION}.{RULES_VERSION}"

NOT_APPLICABLE_FINANCIALS = "not_applicable_financials"
BANK_SIC = (6000, 6099)
BANK_NOT_APPLICABLE = ("revenue", "gross_profit")

type PeriodType = Literal["Q", "FY"]
type MetricKey = tuple[str, str, date]  # metric, period type, period end


@dataclass(frozen=True)
class MetricRow:
    """A `financial_metrics` row."""

    cik: int
    metric: str
    period_type: PeriodType
    fiscal_year: int
    fiscal_period: str  # Q1..Q4, FY
    period_start: date | None
    period_end: date
    value: Decimal | None
    unit: str
    concept: str | None
    accession_no: str | None
    source_accessions: tuple[str, ...]
    filed_at: date | None
    quality_flags: tuple[str, ...] = ()
    calc_version: str = CALC_VERSION
    method: str = "reported"

    @property
    def key(self) -> MetricKey:
        return (self.metric, self.period_type, self.period_end)


@dataclass(frozen=True)
class ReportedMetrics:
    rows: tuple[MetricRow, ...]
    notes: tuple[str, ...]  # periods left out, with why


def is_bank(sic_code: str | None) -> bool:
    if sic_code is None or not sic_code.isdigit():
        return False
    return BANK_SIC[0] <= int(sic_code) <= BANK_SIC[1]


def reported_metrics(
    cik: int,
    facts: Iterable[XbrlFact],
    *,
    fiscal_year_end: str | None,
    sic_code: str | None,
    metrics: Sequence[Metric] = METRICS,
    calc_version: str = CALC_VERSION,
) -> ReportedMetrics:
    usable = [fact for fact in facts if fact.form in METRIC_FORMS]
    builder = _Builder(cik, usable, fiscal_year_end, calc_version)
    bank = is_bank(sic_code)
    rows: list[MetricRow] = []
    for metric in metrics:
        if bank and metric.name in BANK_NOT_APPLICABLE:
            continue
        rows += builder.flows(metric) if metric.kind == "flow" else builder.instants(metric)
    if bank:
        rows += builder.not_applicable(
            [metric for metric in metrics if metric.name in BANK_NOT_APPLICABLE], rows
        )
    rows.sort(key=lambda row: (row.metric, row.period_type, row.period_end))
    return ReportedMetrics(tuple(rows), tuple(builder.notes))


class _Builder:
    def __init__(
        self, cik: int, facts: Sequence[XbrlFact], fiscal_year_end: str | None, calc_version: str
    ) -> None:
        self.cik = cik
        self.calc_version = calc_version
        self.calendar = FiscalCalendar.from_facts(facts, fiscal_year_end)
        self.notes: list[str] = []
        latest = latest_facts(facts)
        # (taxonomy, concept, unit) to its facts by (start, end)
        self.facts: dict[tuple[str, str, str], dict[tuple[date | None, date], XbrlFact]]
        self.facts = defaultdict(dict)
        for (taxonomy, concept, unit, start, end), fact in latest.items():
            self.facts[(taxonomy, concept, unit)][(start, end)] = fact
        # A filing's period: the latest end among its periods (a 10-Q's quarter end), from
        # every fact, since a later filing reports most of an old one's periods again.
        self.filing_end: dict[str, date] = {}
        self.period_ends: set[date] = set()
        for fact in facts:
            if classify(fact.period_start, fact.period_end) is None:
                continue
            self.period_ends.add(fact.period_end)
            if fact.period_end > self.filing_end.get(fact.accession_no, date.min):
                self.filing_end[fact.accession_no] = fact.period_end

    def flows(self, metric: Metric) -> list[MetricRow]:
        periods: set[tuple[date, date]] = set()
        for concept in metric.concepts:
            for start, end in self._of(concept, metric.unit):
                if start is not None and classify(start, end) in ("Q", "FY"):
                    periods.add((start, end))
        chosen: dict[tuple[PeriodType, date], tuple[XbrlFact, Concept]] = {}
        for start, end in sorted(periods):
            period_type: PeriodType = "Q" if classify(start, end) == "Q" else "FY"
            if period_type == "FY" and not self.calendar.is_year_end(end):
                self.notes.append(f"{metric.name}: {start} to {end} is no fiscal year")
                continue
            pick = self._first(metric, (start, end))
            if pick is None:
                continue
            kept = chosen.get((period_type, end))
            if kept is not None:
                stub, other = sorted((kept, pick), key=lambda item: _filed(item[0]))
                self.notes.append(
                    f"{metric.name}: {_period(stub[0])} and {_period(other[0])} end on the same "
                    f"day; {other[0].accession_no}, filed later, wins"
                )
                pick = other
            chosen[(period_type, end)] = pick
        rows = []
        for (period_type, end), (fact, concept) in sorted(chosen.items()):
            row = self._row(metric, concept, fact, period_type, fact.period_start, end)
            if row is not None:
                rows.append(row)
        return rows

    def instants(self, metric: Metric) -> list[MetricRow]:
        rows = []
        for end in sorted(self.period_ends):
            pick = self._instant(metric, end)
            if pick is None:
                continue
            fact, concept = pick
            row = self._row(metric, concept, fact, "Q", None, end)
            if row is not None:
                rows.append(row)
            if self.calendar.is_year_end(end):
                year = self._row(metric, concept, fact, "FY", None, end)
                if year is not None:
                    rows.append(year)
        return rows

    def not_applicable(
        self, metrics: Sequence[Metric], rows: Sequence[MetricRow]
    ) -> list[MetricRow]:
        """Null rows with the flag for each period of the company's other flows."""
        flows = {metric.name for metric in METRICS if metric.kind == "flow"}
        periods = {(row.period_type, row.period_end): row for row in rows if row.metric in flows}
        return [
            replace(
                template,
                metric=metric.name,
                value=None,
                unit=metric.unit,
                concept=None,
                accession_no=None,
                source_accessions=(),
                filed_at=None,
                quality_flags=(NOT_APPLICABLE_FINANCIALS,),
            )
            for metric in metrics
            for _, template in sorted(periods.items())
        ]

    def _of(self, concept: Concept, unit: str) -> dict[tuple[date | None, date], XbrlFact]:
        return self.facts.get((concept.taxonomy, concept.name, unit), {})

    def _first(self, metric: Metric, period: tuple[date, date]) -> tuple[XbrlFact, Concept] | None:
        for concept in metric.concepts:
            fact = self._of(concept, metric.unit).get(period)
            if fact is not None:
                return fact, concept
        return None

    def _instant(self, metric: Metric, end: date) -> tuple[XbrlFact, Concept] | None:
        for concept in metric.concepts:
            facts = self._of(concept, metric.unit)
            if concept.cover_page:
                filed = [
                    fact
                    for (start, _), fact in facts.items()
                    if start is None and self.filing_end.get(fact.accession_no) == end
                ]
                if filed:
                    return max(filed, key=_filed), concept
                continue
            fact = facts.get((None, end))
            if fact is not None:
                return fact, concept
        return None

    def _row(
        self,
        metric: Metric,
        concept: Concept,
        fact: XbrlFact,
        period_type: PeriodType,
        start: date | None,
        end: date,
    ) -> MetricRow | None:
        if period_type == "FY":
            fiscal_year, fiscal_period = self.calendar.year(end), "FY"
        else:
            label = self.calendar.quarter(end)
            if label is None:
                self.notes.append(f"{metric.name}: no fiscal year end to label {end}")
                return None
            fiscal_year, fiscal_period = label.fiscal_year, label.fiscal_period
        return MetricRow(
            cik=self.cik,
            metric=metric.name,
            period_type=period_type,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            period_start=start,
            period_end=end,
            value=fact.value,
            unit=metric.unit,
            concept=concept.qualified,
            accession_no=fact.accession_no,
            source_accessions=(fact.accession_no,),
            filed_at=fact.filed_at,
            quality_flags=(concept.flag,) if concept.flag else (),
            calc_version=self.calc_version,
        )


def _filed(fact: XbrlFact) -> tuple[date, str]:
    return fact.filed_at, fact.accession_no


def _period(fact: XbrlFact) -> str:
    return f"{fact.period_start} to {fact.period_end}"
