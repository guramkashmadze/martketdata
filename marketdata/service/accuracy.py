"""The accuracy report (PLAN.md §9.10; E5b-S4): `marketdata quality accuracy` and
`GET /v1/status` publish, from the data as it is stored now:

- the golden-set match rate per earnings slot and per metric: the hand-checked values
  (golden_values, loaded from tests/golden/ by the CLI) that the platform serves exactly; a
  known difference counts as a miss, a value that doesn't apply isn't counted;
- the cross-check match rate: release values equal to the later 10-Q or 10-K, of those
  compared (D-028), each mismatch listed with both values; the 0.5% tolerance stays as D-028
  set it, so a mismatch the filing's rounding explains (CoreWeave's 10-K in millions) is still
  a mismatch;
- the extraction pass rate: slots that passed every check, of those that apply (§9.9);
- the median and 90th-percentile minutes from SEC acceptance to the first verified value, over
  the releases the watcher routed when they were filed: a backfill verifies a release months
  after it was filed, which says nothing about speed;
- value changes by reason over the last 90 days.

A rate is a percentage to one decimal, null when nothing was counted.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import earnings, financial_metrics, golden, value_changes
from marketdata.extract.interface import SLOTS
from marketdata.service.companies import group_of
from marketdata.service.extraction import extraction_report
from marketdata.service.quality import Mismatch, cross_check_summary
from marketdata.service.served import Number

CHANGE_DAYS = 90
REASONS = ("correction", "restatement", "amendment")
TENTH = Decimal("0.1")


class Rate(BaseModel):
    model_config = ConfigDict(frozen=True)

    matched: int
    checked: int
    rate: Number | None  # percent, one decimal; null when nothing was checked


class GoldenAccuracy(BaseModel):
    model_config = ConfigDict(frozen=True)

    values: int  # hand-checked values loaded
    loaded_at: datetime | None
    earnings: Rate  # release slots: the quarter and the year before both served exactly
    slots: dict[str, Rate]  # revenue, operating_income, net_income, eps_diluted
    metrics_total: Rate
    metrics: dict[str, Rate]
    known_differences: int  # misses listed with their reason in the golden files
    not_applicable: int  # values that don't apply, not counted
    not_stored: int  # values whose release or company isn't stored in this database


class CrossCheckAccuracy(BaseModel):
    model_config = ConfigDict(frozen=True)

    statuses: dict[str, int]  # slots by status: matched, mismatch, not_comparable, pending
    rate: Number | None  # matched over matched and mismatched
    mismatches: tuple[Mismatch, ...]


class ExtractionAccuracy(BaseModel):
    model_config = ConfigDict(frozen=True)

    releases: int
    statuses: dict[str, int]  # complete, partial, failed, no_statement
    rules_versions: tuple[str, ...]
    total: Rate  # passed slots of those that apply, in releases with a statement
    slots: dict[str, Rate]


class Latency(BaseModel):
    model_config = ConfigDict(frozen=True)

    releases: int  # routed live by the watcher and verified
    median_minutes: Number | None
    p90_minutes: Number | None


class ChangeCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    since: datetime  # the last 90 days
    earnings_facts: dict[str, int]  # by reason
    financial_metrics: dict[str, int]
    insider_trades: dict[str, int]  # E8: by reason, correction or amendment


class AccuracyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    golden: GoldenAccuracy
    cross_check: CrossCheckAccuracy
    extraction: ExtractionAccuracy
    latency: Latency
    changes: ChangeCounts


def rate(matched: int, checked: int) -> Rate:
    percent = (
        (Decimal(100 * matched) / checked).quantize(TENTH, rounding=ROUND_HALF_UP)
        if checked
        else None
    )
    return Rate(matched=matched, checked=checked, rate=percent)


async def accuracy_report(
    conn: AsyncConnection[Any], *, now: datetime | None = None
) -> AccuracyReport:
    now = now or datetime.now(UTC)
    checks = await cross_check_summary(conn)
    extraction = await extraction_report(conn)
    since = now - timedelta(days=CHANGE_DAYS)
    counted = await value_changes.count_by_reason(conn, since)
    return AccuracyReport(
        generated_at=now,
        golden=await golden_accuracy(conn),
        cross_check=CrossCheckAccuracy(
            statuses=checks.statuses, rate=checks.match_rate, mismatches=checks.mismatches
        ),
        extraction=ExtractionAccuracy(
            releases=extraction.releases,
            statuses=extraction.statuses,
            rules_versions=extraction.rules_versions,
            total=rate(extraction.total.passed, extraction.total.applicable),
            slots={
                slot: rate(counts.passed, counts.applicable)
                for slot, counts in extraction.slots.items()
            },
        ),
        latency=latency(await earnings.live_latencies(conn)),
        changes=ChangeCounts(
            since=since,
            earnings_facts={
                reason: counted.get(("earnings_fact", reason), 0) for reason in REASONS
            },
            insider_trades={
                reason: counted.get(("insider_transaction", reason), 0)
                for reason in ("correction", "amendment")
            },
            financial_metrics={
                reason: counted.get(("financial_metric", reason), 0) for reason in REASONS
            },
        ),
    )


async def golden_accuracy(conn: AsyncConnection[Any]) -> GoldenAccuracy:
    """The golden values against what is served now."""
    stored = await golden.all_values(conn)
    released = [value for value in stored if value.kind == "earnings"]
    facts = {
        (fact.accession_no, fact.slot): fact
        for fact in await earnings.facts_of(
            conn, sorted({str(value.accession_no) for value in released})
        )
    }
    reported = [value for value in stored if value.kind == "metric"]
    groups = {cik: await group_of(conn, cik) for cik in {value.cik for value in reported}}
    keys: list[tuple[int, str, str, date]] = [
        (member, str(value.metric), str(value.period_type), value.period_end)
        for value in reported
        if value.period_end is not None
        for member in groups[value.cik]
    ]
    rows = {
        (row.cik, row.metric, row.period_type, row.period_end): row.value
        for row in await financial_metrics.rows_at(conn, keys)
    }
    with_rows = set(await financial_metrics.row_counts(conn, sorted({key[0] for key in keys})))
    slots: dict[str, list[bool]] = {slot: [] for slot in SLOTS}
    metrics: dict[str, list[bool]] = {}
    not_applicable = not_stored = 0
    for value in released:
        if value.status == "not_applicable":
            not_applicable += 1
            continue
        fact = facts.get((str(value.accession_no), str(value.slot)))
        if fact is None:
            not_stored += 1
            continue
        slots.setdefault(str(value.slot), []).append(
            fact.check_status == "passed"
            and fact.value == value.value
            and fact.prior_value == value.prior_value
        )
    for value in reported:
        if value.status == "not_applicable":
            not_applicable += 1
            continue
        group = groups[value.cik]
        if not with_rows & set(group) or value.period_end is None:
            not_stored += 1
            continue
        at = [
            (member, str(value.metric), str(value.period_type), value.period_end)
            for member in group
        ]
        served = next((rows[key] for key in at if key in rows), None)
        metrics.setdefault(str(value.metric), []).append(
            served is not None and served == value.value
        )
    return GoldenAccuracy(
        values=len(stored),
        loaded_at=max((value.loaded_at for value in stored), default=None),
        earnings=_total(slots.values()),
        slots={slot: rate(sum(found), len(found)) for slot, found in slots.items()},
        metrics_total=_total(metrics.values()),
        metrics={metric: rate(sum(found), len(found)) for metric, found in sorted(metrics.items())},
        known_differences=sum(value.status == "known_difference" for value in stored),
        not_applicable=not_applicable,
        not_stored=not_stored,
    )


def latency(seconds: Sequence[Decimal]) -> Latency:
    """The median and 90th percentile, interpolated between the nearest two as PostgreSQL's
    percentile_cont does, in minutes to one decimal."""
    ordered = sorted(seconds)
    return Latency(
        releases=len(ordered),
        median_minutes=_minutes(_percentile(ordered, Decimal("0.5"))),
        p90_minutes=_minutes(_percentile(ordered, Decimal("0.9"))),
    )


def _percentile(ordered: Sequence[Decimal], fraction: Decimal) -> Decimal | None:
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _minutes(seconds: Decimal | None) -> Decimal | None:
    if seconds is None:
        return None
    return (seconds / 60).quantize(TENTH, rounding=ROUND_HALF_UP)


def _total(found: Iterable[list[bool]]) -> Rate:
    checks = [check for group in found for check in group]
    return rate(sum(checks), len(checks))
