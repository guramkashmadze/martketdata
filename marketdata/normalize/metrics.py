"""Metric rows from XBRL facts: reported (E4a-S4) and derived quarters (E4b-S1).

Pure. For one company, from its stored facts (normalize/companyfacts.py), the metric catalog
(normalize/metric_catalog.py) and the period rules (normalize/periods.py):

1. Only facts of the 10-K and 10-Q family count, and of each concept, unit and period only the
   one filed last (§9.4 rule 2; D-083).
2. Flows: a row for every three-month period (period type Q) and every fiscal year (FY) that a
   catalog concept reports, from the first concept in the metric's order with a fact for that
   period (rule 3). Two periods ending on the same day (a stub) give the one filed last.
3. Derived quarters (rules 3-5, E4b-S1): a quarter that no three-month fact reports is the
   difference of two facts with the same start, the longer one ending on the quarter's end:
   six months less the first quarter and nine months less six (`derived_ytd`), the year less
   nine months (`derived_q4`), or, when no nine-month fact exists, the year less the three
   quarters before it. One derivation reads one concept: the first in the metric's order that
   has every part. When the parts exist only under different concepts, the quarter is null
   with `concept_mismatch`. A reported three-month fact always wins (Accenture's and Target's
   fourth quarters, D-084). Per-share and average-share metrics are never derived: a fourth
   quarter that isn't reported is null with `eps_q4_not_reported`.
4. Instants: a row at every quarter end (Q1-Q4) and every fiscal year end (FY, the same value
   as Q4). A quarter end is the end of any period the company reports. The cover-page share
   count sits at the end of the period its filing reports (§9.3's note).
5. Banks (SIC 6000-6099) have no revenue or gross profit, as in their releases (D-072): null
   with `not_applicable_financials` for each period of their other flows (rule 10, D-084).
6. A money metric whose facts are all in another currency is null with `non_usd_reporter`
   for each period they report (rule 9).
7. Fiscal labels come from the company's own year ends (rule 7).

Every row has its method, its concept, the filings it reads (`accession_no` the latest of
them, `filed_at` its date) and CALC_VERSION: the catalog version, then the version of these
rules. normalize/financials.py adds the computed rows, TTM and the quality checks (E4b).
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.metric_catalog import (
    CATALOG_VERSION,
    METRICS,
    SUPPORTING,
    Concept,
    Metric,
)
from marketdata.normalize.periods import (
    METRIC_FORMS,
    FiscalCalendar,
    classify,
    latest_facts,
)

RULES_VERSION = "2"
CALC_VERSION = f"{CATALOG_VERSION}.{RULES_VERSION}"

NOT_APPLICABLE_FINANCIALS = "not_applicable_financials"
CONCEPT_MISMATCH = "concept_mismatch"
EPS_Q4_NOT_REPORTED = "eps_q4_not_reported"
NON_USD_REPORTER = "non_usd_reporter"
# Flags of a value that doesn't apply to the company, as opposed to one that is missing.
NOT_APPLICABLE_FLAGS = frozenset({NOT_APPLICABLE_FINANCIALS, NON_USD_REPORTER})

BANK_SIC = (6000, 6099)
BANK_NOT_APPLICABLE = ("revenue", "gross_profit")

DAY = timedelta(days=1)
_FOREIGN_UNIT = {
    "USD": re.compile(r"^[A-Z]{3}$"),
    "USD/shares": re.compile(r"^[A-Z]{3}/shares$"),
}

type PeriodType = Literal["Q", "FY", "TTM"]
type Method = Literal["reported", "derived_ytd", "derived_q4", "derived_ttm", "computed"]
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
    method: Method = "reported"

    @property
    def key(self) -> MetricKey:
        return (self.metric, self.period_type, self.period_end)


@dataclass(frozen=True)
class ReportedMetrics:
    rows: tuple[MetricRow, ...]
    notes: tuple[str, ...]  # periods left out, with why


@dataclass(frozen=True)
class Derivation:
    """A quarter computed from facts of one concept."""

    concept: Concept
    method: Literal["derived_ytd", "derived_q4"]
    start: date
    end: date
    value: Decimal
    facts: tuple[XbrlFact, ...]  # every fact read, the longer period's first


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
    """The rows the company's filings report as they are (E4a-S4): rules 1, 2, 4, 5 and 7."""
    builder = MetricBuilder(cik, facts, fiscal_year_end, calc_version)
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


class MetricBuilder:
    """One company's facts, indexed for the rules above."""

    def __init__(
        self,
        cik: int,
        facts: Iterable[XbrlFact],
        fiscal_year_end: str | None,
        calc_version: str = CALC_VERSION,
    ) -> None:
        usable = [fact for fact in facts if fact.form in METRIC_FORMS]
        self.cik = cik
        self.calc_version = calc_version
        self.calendar = FiscalCalendar.from_facts(usable, fiscal_year_end)
        self.notes: list[str] = []
        # (taxonomy, concept, unit) to its facts by (start, end), the latest filing of each
        self.facts: dict[tuple[str, str, str], dict[tuple[date | None, date], XbrlFact]]
        self.facts = defaultdict(dict)
        self.units: dict[tuple[str, str], set[str]] = defaultdict(set)
        for (taxonomy, concept, unit, start, end), fact in latest_facts(usable).items():
            self.facts[(taxonomy, concept, unit)][(start, end)] = fact
            self.units[(taxonomy, concept)].add(unit)
        # A filing's period: the latest end among its periods (a 10-Q's quarter end), from
        # every fact, since a later filing reports most of an old one's periods again.
        self.filing_end: dict[str, date] = {}
        self.period_ends: set[date] = set()
        self.nine_months: dict[date, set[date]] = defaultdict(set)  # start to 9M ends
        for fact in usable:
            kind = classify(fact.period_start, fact.period_end)
            if kind is None:
                continue
            self.period_ends.add(fact.period_end)
            if fact.period_end > self.filing_end.get(fact.accession_no, date.min):
                self.filing_end[fact.accession_no] = fact.period_end
            if kind == "9M" and fact.period_start is not None:
                self.nine_months[fact.period_start].add(fact.period_end)

    # Flows

    def flows(self, metric: Metric, *, derive: bool = False) -> list[MetricRow]:
        """Rule 2's rows; with `derive`, rule 3's quarters too (E4b)."""
        rows = self._reported_flows(metric)
        if not derive:
            return rows
        quarters = {row.period_end for row in rows if row.period_type == "Q"}
        if metric.per_share:
            years = [row for row in rows if row.period_type == "FY"]
            return rows + self._fourth_quarters_not_reported(metric, years, quarters)
        return rows + self._derived(metric, quarters)

    def _reported_flows(self, metric: Metric) -> list[MetricRow]:
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

    def _derived(self, metric: Metric, reported: set[date]) -> list[MetricRow]:
        picks: dict[date, Derivation] = {}
        for derivation in self.derivations(metric):
            if derivation.end not in reported:
                picks.setdefault(derivation.end, derivation)
        rows = [self._derived_row(metric, derivation) for derivation in picks.values()]
        for start, end, method, concepts in self._mismatches(metric):
            if end in reported or end in picks:
                continue
            self.notes.append(
                f"{metric.name}: the quarter {start} to {end} needs facts of "
                f"{' and '.join(concepts)}; never mixed (concept_mismatch)"
            )
            rows.append(self._null_row(metric, "Q", start, end, method, CONCEPT_MISMATCH))
        return sorted((row for row in rows if row is not None), key=lambda row: row.period_end)

    def derivations(self, metric: Metric) -> list[Derivation]:
        """Every quarter the metric's facts can derive, in the order rules 3-5 prefer them:
        year-to-date differences by concept (within a concept, the latest filed first), then
        a year less its first three quarters."""
        ordered: list[Derivation] = []
        quarters: dict[Concept, dict[date, Derivation]] = {}
        for concept in metric.concepts:
            differences = sorted(
                self._differences(concept, metric.unit),
                key=lambda item: (item.end, _filed(item.facts[0])),
                reverse=True,
            )
            ordered += differences
            quarters[concept] = self._quarters(concept, metric.unit, differences)
        for concept in metric.concepts:
            ordered += self._years_less_quarters(concept, metric.unit, quarters[concept])
        return ordered

    def _differences(self, concept: Concept, unit: str) -> Iterator[Derivation]:
        """Each pair of facts with the same start whose difference is a quarter."""
        by_start: dict[date, list[XbrlFact]] = defaultdict(list)
        for (start, end), fact in self._of(concept, unit).items():
            if start is not None and self._cumulative(start, end):
                by_start[start].append(fact)
        for start, facts in by_start.items():
            facts.sort(key=lambda fact: fact.period_end)
            for shorter, longer in pairwise(facts):
                quarter_start = shorter.period_end + DAY
                kind = classify(start, longer.period_end)
                if classify(quarter_start, longer.period_end) != "Q" or kind == "Q":
                    continue
                yield Derivation(
                    concept,
                    "derived_q4" if kind == "FY" else "derived_ytd",
                    quarter_start,
                    longer.period_end,
                    longer.value - shorter.value,
                    (longer, shorter),
                )

    def _quarters(
        self, concept: Concept, unit: str, differences: Sequence[Derivation]
    ) -> dict[date, Derivation]:
        """The concept's quarters by their first day: reported three-month facts (the latest
        filed of a start), then its year-to-date differences. A reported quarter is held as a
        derivation of one fact."""
        quarters: dict[date, Derivation] = {}
        reported = sorted(
            (
                fact
                for (start, end), fact in self._of(concept, unit).items()
                if start is not None and classify(start, end) == "Q"
            ),
            key=_filed,
            reverse=True,
        )
        for fact in reported:
            assert fact.period_start is not None
            quarters.setdefault(
                fact.period_start,
                Derivation(
                    concept, "derived_ytd", fact.period_start, fact.period_end, fact.value, (fact,)
                ),
            )
        for derivation in differences:
            if derivation.method == "derived_ytd":
                quarters.setdefault(derivation.start, derivation)
        return quarters

    def _years_less_quarters(
        self, concept: Concept, unit: str, quarters: dict[date, Derivation]
    ) -> Iterator[Derivation]:
        """Rule 5's fallback: a year less the three quarters that start it."""
        for (start, end), year in self._of(concept, unit).items():
            if start is None or classify(start, end) != "FY" or not self.calendar.is_year_end(end):
                continue
            parts: list[Derivation] = []
            cursor = start
            while len(parts) < 3 and cursor in quarters:
                parts.append(quarters[cursor])
                cursor = quarters[cursor].end + DAY
            if len(parts) == 3 and classify(cursor, end) == "Q":
                yield Derivation(
                    concept,
                    "derived_q4",
                    cursor,
                    end,
                    year.value - sum((part.value for part in parts), Decimal(0)),
                    (year, *(fact for part in parts for fact in part.facts)),
                )

    def _mismatches(
        self, metric: Metric
    ) -> Iterator[tuple[date, date, Literal["derived_ytd", "derived_q4"], tuple[str, ...]]]:
        """Quarters whose year-to-date parts exist only under different concepts."""
        by_start: dict[date, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
        for concept in metric.concepts:
            for start, end in self._of(concept, metric.unit):
                if start is not None and self._cumulative(start, end):
                    by_start[start][end].add(concept.qualified)
        for start, ends in by_start.items():
            for shorter, longer in pairwise(sorted(ends)):
                quarter_start = shorter + DAY
                kind = classify(start, longer)
                if classify(quarter_start, longer) != "Q" or kind == "Q":
                    continue
                if ends[shorter] & ends[longer]:
                    continue
                method: Literal["derived_ytd", "derived_q4"] = (
                    "derived_q4" if kind == "FY" else "derived_ytd"
                )
                concepts = tuple(sorted(ends[shorter] | ends[longer]))
                yield quarter_start, longer, method, concepts

    def _cumulative(self, start: date, end: date) -> bool:
        """A period a derivation can read: a quarter, six or nine months, or a fiscal year."""
        kind = classify(start, end)
        return kind is not None and (kind != "FY" or self.calendar.is_year_end(end))

    def _fourth_quarters_not_reported(
        self, metric: Metric, years: Sequence[MetricRow], reported: set[date]
    ) -> list[MetricRow]:
        """Rule 5: per-share and average-share metrics are never derived by subtraction."""
        rows = []
        for year in years:
            if year.period_end in reported or year.period_start is None:
                continue
            start = self._fourth_quarter_start(year.period_start, year.period_end)
            row = self._null_row(
                metric, "Q", start, year.period_end, "derived_q4", EPS_Q4_NOT_REPORTED
            )
            if row is not None:
                rows.append(row)
        return rows

    def _fourth_quarter_start(self, start: date, end: date) -> date | None:
        """The day after the year's nine months, from any fact that reports them."""
        ends = [
            nine
            for nine in self.nine_months.get(start, ())
            if nine < end and classify(nine + DAY, end) == "Q"
        ]
        return max(ends) + DAY if ends else None

    def _derived_row(self, metric: Metric, derivation: Derivation) -> MetricRow | None:
        label = self.calendar.quarter(derivation.end)
        if label is None:
            self.notes.append(f"{metric.name}: no fiscal year end to label {derivation.end}")
            return None
        facts = sorted(derivation.facts, key=_filed)
        latest = facts[-1]
        concept = derivation.concept
        return MetricRow(
            cik=self.cik,
            metric=metric.name,
            period_type="Q",
            fiscal_year=label.fiscal_year,
            fiscal_period=label.fiscal_period,
            period_start=derivation.start,
            period_end=derivation.end,
            value=derivation.value,
            unit=metric.unit,
            concept=concept.qualified,
            accession_no=latest.accession_no,
            source_accessions=tuple(dict.fromkeys(fact.accession_no for fact in facts)),
            filed_at=latest.filed_at,
            quality_flags=(concept.flag,) if concept.flag else (),
            calc_version=self.calc_version,
            method=derivation.method,
        )

    # Instants

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

    def balances(self) -> dict[date, tuple[Decimal, Decimal]]:
        """Each balance-sheet date's Assets and LiabilitiesAndStockholdersEquity (§9.5)."""
        assets = self._of(Concept("Assets"), "USD")
        totals = {end: fact.value for (start, end), fact in assets.items() if start is None}
        return {
            end: (value, total.value)
            for end, value in totals.items()
            if (total := self.supporting("LiabilitiesAndStockholdersEquity", end)) is not None
        }

    def supporting(self, name: str, end: date) -> XbrlFact | None:
        """A supporting concept's value at `end` (catalog SUPPORTING, in USD)."""
        [concept] = [concept for concept in SUPPORTING if concept.name == name]
        return self._of(concept, "USD").get((None, end))

    def liabilities(self, existing: set[date]) -> list[MetricRow]:
        """§9.3: total liabilities, where the filings tag none, computed as liabilities and
        equity less equity including the noncontrolling interest (method `computed`)."""
        rows = []
        for end in sorted(self.period_ends - existing):
            total = self.supporting("LiabilitiesAndStockholdersEquity", end)
            equity = self.supporting(
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", end
            )
            if total is None or equity is None:
                continue
            facts = sorted((total, equity), key=_filed)
            types: list[PeriodType] = ["Q", "FY"] if self.calendar.is_year_end(end) else ["Q"]
            for period_type in types:
                label = self._label(period_type, end)
                if label is None:
                    continue
                rows.append(
                    MetricRow(
                        cik=self.cik,
                        metric="total_liabilities",
                        period_type=period_type,
                        fiscal_year=label[0],
                        fiscal_period=label[1],
                        period_start=None,
                        period_end=end,
                        value=total.value - equity.value,
                        unit="USD",
                        concept=None,
                        accession_no=facts[-1].accession_no,
                        source_accessions=tuple(dict.fromkeys(f.accession_no for f in facts)),
                        filed_at=facts[-1].filed_at,
                        calc_version=self.calc_version,
                        method="computed",
                    )
                )
        return rows

    # Rows that hold no value

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
                method="reported",
            )
            for metric in metrics
            for _, template in sorted(periods.items())
        ]

    def non_usd(self, metric: Metric) -> list[MetricRow]:
        """Rule 9: null rows with `non_usd_reporter` for each period a money metric reports
        only in another currency."""
        pattern = _FOREIGN_UNIT.get(metric.unit)
        if pattern is None or any(self._of(concept, metric.unit) for concept in metric.concepts):
            return []
        periods: set[tuple[date | None, date]] = set()
        for concept in metric.concepts:
            for unit in self.units.get((concept.taxonomy, concept.name), ()):
                if unit != metric.unit and pattern.match(unit):
                    periods |= set(self.facts[(concept.taxonomy, concept.name, unit)])
        rows = []
        for start, end in sorted(periods, key=lambda period: (period[1], period[0] or date.min)):
            kind = classify(start, end)
            if metric.kind == "flow":
                if kind == "Q":
                    types: list[PeriodType] = ["Q"]
                elif kind == "FY" and self.calendar.is_year_end(end):
                    types = ["FY"]
                else:
                    continue
            elif start is None and end in self.period_ends:
                types = ["Q", "FY"] if self.calendar.is_year_end(end) else ["Q"]
            else:
                continue
            for period_type in types:
                row = self._null_row(metric, period_type, start, end, "reported", NON_USD_REPORTER)
                if row is not None:
                    rows.append(row)
        return rows

    def _null_row(
        self,
        metric: Metric,
        period_type: PeriodType,
        start: date | None,
        end: date,
        method: Method,
        flag: str,
    ) -> MetricRow | None:
        label = self._label(period_type, end)
        if label is None:
            self.notes.append(f"{metric.name}: no fiscal year end to label {end}")
            return None
        return MetricRow(
            cik=self.cik,
            metric=metric.name,
            period_type=period_type,
            fiscal_year=label[0],
            fiscal_period=label[1],
            period_start=start,
            period_end=end,
            value=None,
            unit=metric.unit,
            concept=None,
            accession_no=None,
            source_accessions=(),
            filed_at=None,
            quality_flags=(flag,),
            calc_version=self.calc_version,
            method=method,
        )

    # Shared

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

    def _label(self, period_type: PeriodType, end: date) -> tuple[int, str] | None:
        if period_type == "FY":
            return self.calendar.year(end), "FY"
        label = self.calendar.quarter(end)
        return (label.fiscal_year, label.fiscal_period) if label else None

    def _row(
        self,
        metric: Metric,
        concept: Concept,
        fact: XbrlFact,
        period_type: PeriodType,
        start: date | None,
        end: date,
    ) -> MetricRow | None:
        label = self._label(period_type, end)
        if label is None:
            self.notes.append(f"{metric.name}: no fiscal year end to label {end}")
            return None
        return MetricRow(
            cik=self.cik,
            metric=metric.name,
            period_type=period_type,
            fiscal_year=label[0],
            fiscal_period=label[1],
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
