"""A company's metric rows (E4b): every rule of PLAN.md §9.3-§9.5, in order.

Pure. From the company's stored facts:

1. reported quarters, years and instants, the quarters derived from year-to-date facts and
   money metrics reported only in another currency (normalize/metrics.py, §9.4 rules 1-7, 9);
2. total liabilities computed where the filings tag none (§9.3), and banks' revenue and gross
   profit not applicable (rule 10);
3. gross profit computed where none is tagged, TTM, then the computed metrics
   (normalize/computed.py, rule 8 and §9.3);
4. the quality checks (normalize/quality.py, §9.5).

A company's predecessors' facts (D-068) fill the periods its own filings don't report, so its
quarters can be derived across a succession: XOM's six months to 2026-06-30 are under
ExxonMobil Holdings (2115436) and its first quarter under Exxon Mobil Corp (34088). Given
`predecessor_facts`, the rows are built over the company's facts and the predecessors' facts of
other periods, and the company keeps the rows that read one of its own filings, and the rows
without a value at a period one of those covers; each predecessor's own rows are built from
its facts alone, as in E4a (D-084).

`facts_refresh` and `marketdata normalize --all` store the result (ingest/facts.py).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.computed import computed_rows, gross_profit_rows, ttm_rows
from marketdata.normalize.metric_catalog import METRICS, Metric
from marketdata.normalize.metrics import (
    BANK_NOT_APPLICABLE,
    CALC_VERSION,
    MetricBuilder,
    MetricRow,
    is_bank,
)
from marketdata.normalize.quality import check


@dataclass(frozen=True)
class BuiltMetrics:
    rows: tuple[MetricRow, ...]
    notes: tuple[str, ...]  # periods left out, with why


def build_metrics(
    cik: int,
    facts: Iterable[XbrlFact],
    *,
    fiscal_year_end: str | None,
    sic_code: str | None,
    metrics: Sequence[Metric] = METRICS,
    calc_version: str = CALC_VERSION,
    predecessor_facts: Iterable[XbrlFact] = (),
) -> BuiltMetrics:
    own = list(facts)
    covered = {(fact.period_start, fact.period_end) for fact in own}
    earlier = [
        fact for fact in predecessor_facts if (fact.period_start, fact.period_end) not in covered
    ]
    builder = MetricBuilder(cik, [*own, *earlier], fiscal_year_end, calc_version)
    bank = is_bank(sic_code)
    rows: list[MetricRow] = []
    for metric in metrics:
        if bank and metric.name in BANK_NOT_APPLICABLE:
            continue
        if metric.kind == "flow":
            rows += builder.flows(metric, derive=True)
        else:
            rows += builder.instants(metric)
        rows += builder.non_usd(metric)
    if any(metric.name == "total_liabilities" for metric in metrics):
        rows += builder.liabilities(
            {row.period_end for row in rows if row.metric == "total_liabilities"}
        )
    if bank:
        rows += builder.not_applicable(
            [metric for metric in metrics if metric.name in BANK_NOT_APPLICABLE], rows
        )
    rows += gross_profit_rows(rows)
    rows += ttm_rows(rows)
    rows += computed_rows(rows)
    rows = check(rows, builder.balances())
    if earlier:
        rows = _own(rows, {fact.accession_no for fact in own})
    rows.sort(key=lambda row: (row.metric, row.period_type, row.period_end))
    return BuiltMetrics(tuple(rows), tuple(builder.notes))


def _own(rows: Sequence[MetricRow], accessions: set[str]) -> list[MetricRow]:
    """The rows that read one of the company's own filings, and the rows without a value at a
    period where one of those sits."""
    kept = [row for row in rows if accessions.intersection(row.source_accessions)]
    periods = {(row.period_type, row.period_end) for row in kept}
    return kept + [
        row
        for row in rows
        if not row.source_accessions and (row.period_type, row.period_end) in periods
    ]
