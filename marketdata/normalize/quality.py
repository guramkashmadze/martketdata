"""Quality checks on a company's metric rows (E4b-S3, PLAN.md §9.5).

Pure. Each check adds a flag code to the rows it questions; a flag never changes or hides a
value. The other §9.5 codes are set where the value is made: `concept_mismatch`,
`eps_q4_not_reported` and `non_usd_reporter` (normalize/metrics.py), `ttm_gap`
(normalize/computed.py), the concept flags (normalize/metric_catalog.py) and
`release_vs_filing_mismatch` (verify/cross_check.py, on earnings facts).

- `balance_mismatch`: at a balance-sheet date, |Assets - LiabilitiesAndStockholdersEquity| /
  Assets > 1%; set on total assets, total liabilities and total equity there.
- `quarters_dont_sum`: a year whose four quarters are all reported (three-month facts, none
  derived) and add up to more than 0.5% off the year; set on the year and its quarters. Per-
  share and average-share metrics are left out: a year's earnings per share is no sum of its
  quarters'.
- `outlier_jump`: revenue, total assets, total equity or shares outstanding whose absolute
  size is more than five times, or less than a fifth of, the previous period's of the same type
  (a zero on either side is not compared); set on the later period. Other metrics are left out
  (D-094): flows such as net income and cash flows swing around zero, and on all metrics the
  check flagged 4,311 rows, too many to find a scale error among.
- `stale_data`: the company's latest 10-Q or 10-K that we have facts of is more than 120 days
  old. It depends on the day it is read, so it is decided when values are read
  (`is_stale`), not stored.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

from marketdata.normalize.metric_catalog import BY_NAME
from marketdata.normalize.metrics import DAY, MetricKey, MetricRow

BALANCE_MISMATCH = "balance_mismatch"
QUARTERS_DONT_SUM = "quarters_dont_sum"
OUTLIER_JUMP = "outlier_jump"
STALE_DATA = "stale_data"

# Every §9.5 code, in the table's order, with what it says about a value.
DESCRIPTIONS: Mapping[str, str] = {
    BALANCE_MISMATCH: "assets and liabilities plus equity differ by more than 1%",
    QUARTERS_DONT_SUM: "four reported quarters differ from the year by more than 0.5%",
    OUTLIER_JUMP: "more than 5 times, or under a fifth of, the previous period's size",
    "concept_mismatch": "the year-to-date parts exist only under different concepts",
    "ttm_gap": "no four contiguous quarters with values",
    STALE_DATA: "the latest 10-Q or 10-K is more than 120 days old",
    "eps_q4_not_reported": "a fourth-quarter per-share value is never derived",
    "net_income_includes_nci": "net income includes noncontrolling interests (ProfitLoss)",
    "net_income_to_common": "net income available to common stockholders",
    "cash_includes_restricted": "cash includes restricted cash",
    "non_usd_reporter": "reported in another currency",
    "not_applicable_financials": "a bank's revenue and gross profit don't apply",
    "release_vs_filing_mismatch": "the release differs from the later 10-Q or 10-K",
    "scale_unknown": "the release states no scale",
    "sign_change": "the value and the year before have opposite signs",
    "price_not_licensed": "the price license doesn't cover this channel",
}

BALANCE_TOLERANCE = Decimal("0.01")
SUM_TOLERANCE = Decimal("0.005")
JUMP = Decimal(5)
STALE_AFTER = timedelta(days=120)
BALANCE_METRICS = ("total_assets", "total_liabilities", "total_equity")
# The metrics whose jumps point to a scale error rather than a season or a swing around zero.
JUMP_METRICS = frozenset({"revenue", "total_assets", "total_equity", "shares_outstanding"})


def check(
    rows: Sequence[MetricRow], balances: Mapping[date, tuple[Decimal, Decimal]]
) -> list[MetricRow]:
    """The rows with the flags of every stored check added."""
    flags: dict[MetricKey, set[str]] = defaultdict(set)
    for key in balance_mismatches(rows, balances):
        flags[key].add(BALANCE_MISMATCH)
    for key in quarters_dont_sum(rows):
        flags[key].add(QUARTERS_DONT_SUM)
    for key in outlier_jumps(rows):
        flags[key].add(OUTLIER_JUMP)
    return [_flagged(row, flags[row.key]) if row.key in flags else row for row in rows]


def balance_mismatches(
    rows: Sequence[MetricRow], balances: Mapping[date, tuple[Decimal, Decimal]]
) -> list[MetricKey]:
    """`balances`: each balance-sheet date's Assets and LiabilitiesAndStockholdersEquity."""
    off = {
        end
        for end, (assets, total) in balances.items()
        if assets != 0 and abs(assets - total) / abs(assets) > BALANCE_TOLERANCE
    }
    return [row.key for row in rows if row.metric in BALANCE_METRICS and row.period_end in off]


def quarters_dont_sum(rows: Sequence[MetricRow]) -> list[MetricKey]:
    quarters: dict[tuple[str, date], MetricRow] = {}
    for row in rows:
        if row.period_type == "Q" and row.period_start is not None:
            quarters[(row.metric, row.period_start)] = row
    keys: list[MetricKey] = []
    for year in rows:
        metric = BY_NAME.get(year.metric)
        if (
            year.period_type != "FY"
            or metric is None
            or metric.kind != "flow"
            or metric.per_share
            or year.value is None
            or year.value == 0
            or year.period_start is None
        ):
            continue
        parts: list[MetricRow] = []
        cursor = year.period_start
        while len(parts) < 4 and (part := quarters.get((year.metric, cursor))) is not None:
            parts.append(part)
            cursor = part.period_end + DAY
        if (
            len(parts) < 4
            or parts[-1].period_end != year.period_end
            or any(part.method != "reported" or part.value is None for part in parts)
        ):
            continue
        total = sum((part.value for part in parts if part.value is not None), Decimal(0))
        if abs(total - year.value) / abs(year.value) > SUM_TOLERANCE:
            keys += [year.key, *(part.key for part in parts)]
    return keys


def outlier_jumps(rows: Sequence[MetricRow]) -> list[MetricKey]:
    series: dict[tuple[str, str], list[MetricRow]] = defaultdict(list)
    for row in rows:
        if row.metric in JUMP_METRICS:
            series[(row.metric, row.period_type)].append(row)
    keys: list[MetricKey] = []
    for listed in series.values():
        listed.sort(key=lambda row: row.period_end)
        for before, after in pairwise(listed):
            if not before.value or not after.value:
                continue
            small, large = sorted((abs(before.value), abs(after.value)))
            if large > small * JUMP:
                keys.append(after.key)
    return keys


def is_stale(latest_filed: date | None, today: date) -> bool:
    """§9.5 stale_data: the latest filing we have facts of is more than 120 days old (or there
    is none)."""
    return latest_filed is None or today - latest_filed > STALE_AFTER


def _flagged(row: MetricRow, flags: set[str]) -> MetricRow:
    return replace(row, quality_flags=tuple(sorted(set(row.quality_flags) | flags)))
