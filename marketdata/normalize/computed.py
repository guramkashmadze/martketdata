"""Rows computed from other rows (E4b-S2): gross profit where none is tagged, trailing twelve
months, and §9.3's computed metrics.

Pure. Each function takes a company's metric rows (normalize/metrics.py) and returns new ones:

- Gross profit, where the filings tag none for a quarter or year: revenue less cost of revenue
  (method `computed`, §9.3).
- TTM (§9.4 rule 8): a flow's last four quarters, summed when they are contiguous (each starts
  the day after the one before ends) and all have values (`derived_ttm`). Otherwise the value
  is null with `ttm_gap`, or, when a quarter doesn't apply to the company, with that quarter's
  flag (a bank's revenue). A quarter end has a TTM row once four quarters reach it. Instants
  take the quarter end's value. The average share count over a year is no sum of quarters, so
  `shares_diluted_avg` has no TTM.
- Computed metrics (catalog COMPUTED), for quarters, years and TTM: free cash flow; gross,
  operating and net margins against revenue; and growth against the same period a year
  earlier (the period of the same type ending 350 to 380 days before). Margins and growth are
  percentages rounded to one decimal, like an earnings release's change (§9.9): growth is null
  with `sign_change` when the value and the year before have opposite signs, and there is no
  growth row when the year before is zero, nor a margin when revenue isn't positive.

A computed value is null when an input is null, with the inputs' flags; a value carries the
flags its inputs took from their concepts (net income to common stockholders). Its proof is
the latest filing among its inputs and every filing they read.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from marketdata.normalize.metric_catalog import BY_NAME, METRICS
from marketdata.normalize.metrics import DAY, NOT_APPLICABLE_FLAGS, MetricKey, MetricRow

TTM_GAP = "ttm_gap"
SIGN_CHANGE = "sign_change"
PERCENT = "percent"
PERCENT_PLACES = Decimal("0.1")
HUNDRED = Decimal(100)
YEAR_BEFORE = (timedelta(days=350), timedelta(days=380))
NO_TTM = frozenset({"shares_diluted_avg"})
# Flags a value takes from the concept it was read from (§9.5): carried into computed rows.
CONCEPT_FLAGS = frozenset(
    concept.flag for metric in METRICS for concept in metric.concepts if concept.flag
)
MARGINS = (
    ("gross_margin", "gross_profit"),
    ("operating_margin", "operating_income"),
    ("net_margin", "net_income"),
)
GROWTH = (("revenue_growth_yoy", "revenue"), ("eps_diluted_growth_yoy", "eps_diluted"))


def gross_profit_rows(rows: Sequence[MetricRow]) -> list[MetricRow]:
    """Revenue less cost of revenue for each quarter and year with no gross profit row."""
    index = _index(rows)
    computed = []
    for row in rows:
        if row.metric != "revenue" or row.period_type == "TTM":
            continue
        if ("gross_profit", row.period_type, row.period_end) in index:
            continue
        cost = index.get(("cost_of_revenue", row.period_type, row.period_end))
        if cost is None or row.value is None or cost.value is None:
            continue
        computed += _optional(_combine("gross_profit", "USD", (row, cost), row.value - cost.value))
    return computed


def ttm_rows(rows: Sequence[MetricRow]) -> list[MetricRow]:
    """Rule 8 for every catalog metric with quarter rows."""
    quarters: dict[str, list[MetricRow]] = defaultdict(list)
    for row in rows:
        if row.period_type == "Q":
            quarters[row.metric].append(row)
    made: list[MetricRow] = []
    for name, series in quarters.items():
        metric = BY_NAME.get(name)
        if metric is None or name in NO_TTM:
            continue
        series.sort(key=lambda row: row.period_end)
        if metric.kind == "instant":
            made += [replace(row, period_type="TTM") for row in series]
            continue
        for position in range(3, len(series)):
            made.append(_twelve_months(series[position - 3 : position + 1]))
    return made


def computed_rows(rows: Sequence[MetricRow]) -> list[MetricRow]:
    """§9.3's computed metrics for every quarter, year and TTM their inputs have."""
    index = _index(rows)
    series: dict[tuple[str, str], list[MetricRow]] = defaultdict(list)
    for row in rows:
        series[(row.metric, row.period_type)].append(row)
    made: list[MetricRow] = []
    for row in rows:
        if row.metric == "operating_cash_flow":
            capex = index.get(("capex", row.period_type, row.period_end))
            if capex is not None:
                made += _optional(_fcf(row, capex))
        if row.metric == "revenue":
            for name, numerator in MARGINS:
                part = index.get((numerator, row.period_type, row.period_end))
                if part is not None:
                    made += _optional(_margin(name, part, row))
        for name, metric in GROWTH:
            if row.metric == metric:
                before = _year_before(series[(metric, row.period_type)], row)
                if before is not None:
                    made += _optional(_growth(name, row, before))
    return made


def _twelve_months(window: Sequence[MetricRow]) -> MetricRow:
    flags = {flag for row in window if row.value is None for flag in row.quality_flags}
    contiguous = all(
        after.period_start is not None and after.period_start == before.period_end + DAY
        for before, after in pairwise(window)
    )
    start = window[0].period_start
    template = replace(
        window[-1],
        period_type="TTM",
        period_start=start,
        method="derived_ttm",
        value=None,
        concept=None,
        accession_no=None,
        source_accessions=(),
        filed_at=None,
    )
    if flags & NOT_APPLICABLE_FLAGS:
        return replace(template, quality_flags=tuple(sorted(flags & NOT_APPLICABLE_FLAGS)))
    values = [row.value for row in window if row.value is not None]
    if not contiguous or start is None or len(values) < len(window):
        return replace(template, quality_flags=(TTM_GAP, *sorted(flags - {TTM_GAP})))
    concepts = list(dict.fromkeys(row.concept for row in window if row.concept))
    return _with_inputs(
        replace(template, value=sum(values, Decimal(0)), concept=", ".join(concepts) or None),
        window,
    )


def _fcf(cash: MetricRow, capex: MetricRow) -> MetricRow | None:
    value = None if cash.value is None or capex.value is None else cash.value - capex.value
    return _combine("free_cash_flow", "USD", (cash, capex), value)


def _margin(name: str, part: MetricRow, revenue: MetricRow) -> MetricRow | None:
    if part.value is None or revenue.value is None:
        return _combine(name, PERCENT, (part, revenue), None)
    if revenue.value <= 0:
        return None
    return _combine(name, PERCENT, (part, revenue), _percent(part.value / revenue.value))


def _growth(name: str, row: MetricRow, before: MetricRow) -> MetricRow | None:
    inputs = (row, before)
    if row.value is None or before.value is None:
        return _combine(name, PERCENT, inputs, None, same_period=False)
    if before.value == 0:
        return None
    if row.value * before.value < 0:
        return _combine(name, PERCENT, inputs, None, flags=(SIGN_CHANGE,), same_period=False)
    change = (row.value - before.value) / abs(before.value)
    return _combine(name, PERCENT, inputs, _percent(change), same_period=False)


def _combine(
    name: str,
    unit: str,
    inputs: Sequence[MetricRow],
    value: Decimal | None,
    *,
    flags: Sequence[str] = (),
    same_period: bool = True,
) -> MetricRow | None:
    """A computed row for the first input's period; None when inputs that should cover the
    same period don't, or when a null value would have no flag to say why."""
    first = inputs[0]
    if same_period and any(row.period_start != first.period_start for row in inputs[1:]):
        return None
    row = replace(
        first,
        metric=name,
        unit=unit,
        value=value,
        method="computed",
        concept=None,
        accession_no=None,
        source_accessions=(),
        filed_at=None,
    )
    if value is not None:
        return _with_inputs(row, inputs)
    missing = tuple(flags) or tuple(
        sorted({flag for part in inputs if part.value is None for flag in part.quality_flags})
    )
    return replace(row, quality_flags=missing) if missing else None


def _with_inputs(row: MetricRow, inputs: Sequence[MetricRow]) -> MetricRow:
    """The proof of a value read from `inputs`: the latest filing among them and every filing
    they read; and the flags they took from their concepts."""
    filed = [part for part in inputs if part.filed_at is not None and part.accession_no]
    latest = max(filed, key=lambda part: (part.filed_at, part.accession_no), default=None)
    return replace(
        row,
        accession_no=latest.accession_no if latest else None,
        filed_at=latest.filed_at if latest else None,
        source_accessions=tuple(
            dict.fromkeys(source for part in inputs for source in part.source_accessions)
        ),
        quality_flags=tuple(
            sorted({flag for part in inputs for flag in part.quality_flags} & CONCEPT_FLAGS)
        ),
    )


def _year_before(series: Sequence[MetricRow], row: MetricRow) -> MetricRow | None:
    low, high = YEAR_BEFORE
    candidates = [other for other in series if low <= row.period_end - other.period_end <= high]
    if not candidates:
        return None
    return min(candidates, key=lambda other: abs((row.period_end - other.period_end).days - 365))


def _percent(ratio: Decimal) -> Decimal:
    return (ratio * HUNDRED).quantize(PERCENT_PLACES, rounding=ROUND_HALF_UP)


def _index(rows: Sequence[MetricRow]) -> dict[MetricKey, MetricRow]:
    return {row.key: row for row in rows}


def _optional(row: MetricRow | None) -> list[MetricRow]:
    return [row] if row is not None else []
