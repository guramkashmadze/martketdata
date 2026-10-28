"""Which XBRL value an earnings release is checked against (E4b-S6, D-028, PLAN.md §9.10).

Pure. For each stored release of a company (and its predecessors, D-068):

1. Its quarter: the period end the release states, matched to the end of a period the
   company's 10-Qs and 10-Ks report within a week (a 52/53-week quarter may be printed with
   its nominal date). A release that states no period end (J&J's "THIRD QUARTER 2026", three of
   lululemon's, Berkshire's) takes it from the filings: the latest period a 10-Q or 10-K
   reports that ended at most 90 days before the release (the longest real gap is 61 days,
   D-086) and whose fiscal quarter is the one the release names.
2. The filing for that quarter: the first 10-Q or 10-K whose facts report a period ending
   there. The release is compared with the quarter as that filing gave it: the metric rules
   (normalize/metrics.py) applied to the facts filed up to that day, so a fourth quarter is the
   10-K's year less the nine months, and a restatement a year later is not counted against
   the release.
3. Each slot against the metric of the same name (verify/cross_check.py): `matched` or
   `mismatch`; `not_comparable` when either side has no value (a slot that failed or doesn't
   apply, a fourth-quarter EPS the 10-K doesn't report) or when the filing is listed but its
   facts are not in companyfacts (KO's, V's and NEE's July 2026 10-Qs); `pending` while no
   10-Q or 10-K for the quarter is listed.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.filings import PERIODIC_FORMS
from marketdata.normalize.metric_catalog import BY_NAME
from marketdata.normalize.metrics import MetricBuilder, MetricRow
from marketdata.normalize.periods import METRIC_FORMS, FiscalCalendar, classify
from marketdata.verify.cross_check import NOT_COMPARABLE, PENDING, Status, compare

SLOT_METRICS = {
    "revenue": "revenue",
    "operating_income": "operating_income",
    "net_income": "net_income",
    "eps_diluted": "eps_diluted",
}
# The concepts the checks read, for loading a company's facts.
SLOT_CONCEPTS: Mapping[str, tuple[str, ...]] = {
    "us-gaap": tuple(
        dict.fromkeys(
            concept.name for name in SLOT_METRICS.values() for concept in BY_NAME[name].concepts
        )
    ),
}
RELEASE_WINDOW = timedelta(days=90)  # a release comes within 90 days of its quarter's end
END_SLACK = timedelta(days=7)  # a stated end and the filing's may differ by a few days

_ORDINAL = re.compile(
    r"\b(?:(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter|q([1-4])|([1-4])q)\b",
    re.IGNORECASE,
)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
_ORDINALS |= {"fourth": 4, "4th": 4}


@dataclass(frozen=True)
class ReleaseSlots:
    """What the check needs of a stored release."""

    accession_no: str
    accepted: date | None
    period_end: date | None  # as the release states it
    period_label: str | None
    values: Mapping[str, tuple[str, Decimal | None]]  # slot: (unit, the value it serves)


@dataclass(frozen=True)
class PeriodicFiling:
    """A 10-Q or 10-K of the company, as `filings` lists it."""

    accession_no: str
    form: str
    filed_at: date
    period_of_report: date | None


@dataclass(frozen=True)
class SlotCheck:
    slot: str
    status: Status
    filing_value: Decimal | None
    filing_accession: str | None


@dataclass(frozen=True)
class ReleaseCheck:
    accession_no: str
    quarter_end: date | None
    period_from_filing: bool  # the release states no period end; it was taken from the filing
    note: str
    slots: tuple[SlotCheck, ...]


def check_releases(
    releases: Iterable[ReleaseSlots],
    facts: Iterable[XbrlFact],
    filings: Iterable[PeriodicFiling],
    *,
    fiscal_year_end: str | None,
) -> list[ReleaseCheck]:
    usable = [fact for fact in facts if fact.form in METRIC_FORMS]
    calendar = FiscalCalendar.from_facts(usable, fiscal_year_end)
    first: dict[date, tuple[date, str]] = {}  # period end: the first filing reporting it
    for fact in usable:
        if fact.period_start is None or classify(fact.period_start, fact.period_end) is None:
            continue
        filing = (fact.filed_at, fact.accession_no)
        if fact.period_end not in first or filing < first[fact.period_end]:
            first[fact.period_end] = filing
    periodic = [filing for filing in filings if filing.form in PERIODIC_FORMS]
    as_filed: dict[date, dict[tuple[str, date], MetricRow]] = {}
    checks = []
    for release in releases:
        end, from_filing = _quarter(release, first, periodic, calendar)
        if end is None:
            later = any(
                release.accepted is not None and filing.filed_at >= release.accepted
                for filing in periodic
            )
            note = "the release states no quarter end and no filing gives one"
            checks.append(_all(release, None, False, NOT_COMPARABLE if later else PENDING, note))
            continue
        filed = first.get(end)
        if filed is None:
            listed = [
                filing.accession_no
                for filing in periodic
                if filing.period_of_report is not None
                and abs(filing.period_of_report - end) <= END_SLACK
            ]
            if listed:
                note = f"{', '.join(sorted(listed))} is listed but companyfacts has no facts of it"
                checks.append(_all(release, end, from_filing, NOT_COMPARABLE, note))
            else:
                note = "no 10-Q or 10-K for the quarter yet"
                checks.append(_all(release, end, from_filing, PENDING, note))
            continue
        cutoff = filed[0]
        if cutoff not in as_filed:
            as_filed[cutoff] = _quarters(usable, cutoff, fiscal_year_end)
        rows = as_filed[cutoff]
        slots = []
        for slot, (unit, value) in release.values.items():
            row = rows.get((SLOT_METRICS[slot], end))
            filing_value = row.value if row is not None else None
            accession = row.accession_no if row is not None else None
            slots.append(
                SlotCheck(slot, compare(unit, value, filing_value), filing_value, accession)
            )
        note = f"against {filed[1]}, filed {cutoff}"
        checks.append(ReleaseCheck(release.accession_no, end, from_filing, note, tuple(slots)))
    return checks


def _quarter(
    release: ReleaseSlots,
    first: Mapping[date, tuple[date, str]],
    periodic: Sequence[PeriodicFiling],
    calendar: FiscalCalendar,
) -> tuple[date | None, bool]:
    ends = set(first) | {f.period_of_report for f in periodic if f.period_of_report is not None}
    stated = release.period_end
    if stated is not None:
        near = [end for end in ends if abs(end - stated) <= END_SLACK]
        if not near:
            return stated, False
        return min(near, key=lambda end: abs(end - stated)), False
    ordinal = _ordinal(release.period_label)
    if release.accepted is None or ordinal is None:
        return None, False
    candidates = []
    for end in ends:
        if not timedelta(0) < release.accepted - end <= RELEASE_WINDOW:
            continue
        label = calendar.quarter(end)
        if label is not None and label.fiscal_period == f"Q{ordinal}":
            candidates.append(end)
    return (max(candidates), True) if candidates else (None, False)


def _ordinal(label: str | None) -> int | None:
    """The quarter a release's label names: "THIRD QUARTER 2026", "Q3", "3Q26"."""
    match = _ORDINAL.search(label or "")
    if match is None:
        return None
    word, q_digit, digit_q = match.groups()
    return _ORDINALS[word.lower()] if word else int(q_digit or digit_q)


def _quarters(
    facts: Sequence[XbrlFact], cutoff: date, fiscal_year_end: str | None
) -> dict[tuple[str, date], MetricRow]:
    """The slot metrics' quarters as the facts filed up to `cutoff` give them."""
    builder = MetricBuilder(0, [fact for fact in facts if fact.filed_at <= cutoff], fiscal_year_end)
    rows: dict[tuple[str, date], MetricRow] = {}
    for name in SLOT_METRICS.values():
        for row in builder.flows(BY_NAME[name], derive=True):
            if row.period_type == "Q":
                rows[(name, row.period_end)] = row
    return rows


def _all(
    release: ReleaseSlots, end: date | None, from_filing: bool, status: Status, note: str
) -> ReleaseCheck:
    slots = tuple(SlotCheck(slot, status, None, None) for slot in release.values)
    return ReleaseCheck(release.accession_no, end, from_filing, note, slots)
