"""13F holdings: value units, the cross-manager unit check, amendments, merging, weights and
change types (PLAN.md §9.8, E9). Pure.

Value units (E9-S2). A 13F filed on or after 2023-01-03 reports each value to the nearest
dollar, whatever quarter it is for, amendments of older quarters too; one filed before reports
thousands of dollars (SEC Form 13F FAQ 62, Release 34-95148). Some filers kept reporting
thousands after the change (Baupost, Duquesne Family Office): an override in
data/f13_overrides.toml pins a filer's unit for a range of filing dates, with the evidence.

The unit check (D-023) needs no prices: each manager's implied price, value over shares, is
compared with the median implied price of the curated managers holding the same security the
same way (CUSIP, put or call, shares or principal) that quarter. One more than 50% away from the
median is `unit_suspect`. A security held by fewer than three managers isn't checked: with two,
both would be 50% from their mean as soon as either is wrong. The check flags a value; it never
changes one (§9.5).
"""

import calendar
import re
import statistics
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Literal, cast

from marketdata.normalize.changes import Reason, ValueChange
from marketdata.normalize.form13f import Cover, Holding

VALUE_CUTOFF = date(2023, 1, 3)
ValueUnit = Literal["dollars", "thousands"]
VALUE_UNITS: tuple[ValueUnit, ...] = ("dollars", "thousands")
SCALE: Mapping[ValueUnit, Decimal] = {"dollars": Decimal(1), "thousands": Decimal(1000)}

UNIT_TOLERANCE = Decimal("0.5")  # ±50% of the median implied price (D-023)
MIN_HOLDERS = 3
UNIT_SUSPECT = "unit_suspect"


class UnitOverrideError(ValueError):
    """The 13F unit overrides file has problems; the message lists each."""


@dataclass(frozen=True)
class UnitOverride:
    """A filer's value unit for the filings it made between two dates, and the evidence."""

    manager_cik: int
    filed_from: date
    filed_to: date | None  # inclusive; None: every later filing too
    unit: ValueUnit
    reason: str
    source_url: str  # the SEC filing page that shows it

    def covers(self, manager_cik: int, filed_at: date) -> bool:
        return (
            manager_cik == self.manager_cik
            and self.filed_from <= filed_at
            and (self.filed_to is None or filed_at <= self.filed_to)
        )


@dataclass(frozen=True)
class UnitRule:
    """The unit a filing's values are read in, and why."""

    unit: ValueUnit
    rule: str


def value_unit(
    manager_cik: int, filed_at: date, overrides: Iterable[UnitOverride] = ()
) -> UnitRule:
    """The unit of a 13F's values: an override's, else the filing date's (2023-01-03)."""
    for override in overrides:
        if override.covers(manager_cik, filed_at):
            return UnitRule(override.unit, f"override: {override.reason}")
    if filed_at >= VALUE_CUTOFF:
        return UnitRule("dollars", "filed on or after 2023-01-03: whole dollars (SEC FAQ 62)")
    return UnitRule("thousands", "filed before 2023-01-03: thousands of dollars (SEC FAQ 62)")


def in_dollars(value: Decimal, unit: ValueUnit) -> Decimal:
    return value * SCALE[unit]


def parse_unit_overrides(
    text: str, source: str = "data/f13_overrides.toml"
) -> tuple[UnitOverride, ...]:
    """The `[[unit]]` entries of the overrides file, checked; an empty file means none. Two
    entries for one filer may not cover the same filing date."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UnitOverrideError(f"{source}: {exc}") from None
    entries = document.get("unit", [])
    if not isinstance(entries, list) or set(document) - {"unit"}:
        raise UnitOverrideError(f"{source}: expected only [[unit]] entries")
    overrides: list[UnitOverride] = []
    problems: list[str] = []
    for number, entry in enumerate(entries, start=1):
        try:
            override = _override(entry)
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"unit {number}: {exc}")
            continue
        for earlier, other in enumerate(overrides, start=1):
            if other.manager_cik == override.manager_cik and _overlap(other, override):
                problems.append(
                    f"unit {number}: CIK {override.manager_cik}'s dates overlap unit {earlier}'s"
                )
        overrides.append(override)
    if problems:
        raise UnitOverrideError(f"{source}:\n" + "\n".join(f"  - {line}" for line in problems))
    return tuple(overrides)


_KEYS = {"manager_cik", "filed_from", "filed_to", "unit", "reason", "source_url"}


def _override(entry: object) -> UnitOverride:
    if not isinstance(entry, dict):
        raise TypeError("expected a table")
    unknown = set(entry) - _KEYS
    if unknown:
        raise ValueError(f"unknown keys {sorted(unknown)}")
    cik = entry["manager_cik"]
    if not isinstance(cik, int) or isinstance(cik, bool) or cik <= 0:
        raise ValueError(f"manager_cik must be a CIK (a positive whole number), got {cik!r}")
    filed_from, filed_to = entry["filed_from"], entry.get("filed_to")
    if not isinstance(filed_from, date) or not (filed_to is None or isinstance(filed_to, date)):
        raise ValueError("filed_from and filed_to must be dates (YYYY-MM-DD, unquoted)")
    if filed_to is not None and filed_to < filed_from:
        raise ValueError(f"filed_to {filed_to} is before filed_from {filed_from}")
    unit = entry["unit"]
    if unit not in VALUE_UNITS:
        raise ValueError(f"unit must be one of {', '.join(VALUE_UNITS)}, got {unit!r}")
    reason, source_url = entry["reason"], entry["source_url"]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must give the evidence for the unit")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        raise ValueError("source_url must be the https link to the SEC filing that shows it")
    return UnitOverride(
        cik, filed_from, filed_to, cast(ValueUnit, unit), " ".join(reason.split()), source_url
    )


def _overlap(first: UnitOverride, second: UnitOverride) -> bool:
    first_end, second_end = first.filed_to or date.max, second.filed_to or date.max
    return first.filed_from <= second_end and second.filed_from <= first_end


# --- The cross-manager unit check (D-023).

PriceKey = tuple[str, str | None, str]  # CUSIP, put or call, SH or PRN


@dataclass(frozen=True)
class PricePoint:
    """One manager's position in a security in one quarter, in dollars."""

    manager_cik: int
    cusip: str
    put_call: str | None
    share_type: str
    value_usd: Decimal
    shares: Decimal

    @property
    def key(self) -> PriceKey:
        return (self.cusip, self.put_call, self.share_type)


def unit_suspects(points: Iterable[PricePoint]) -> set[tuple[int, PriceKey]]:
    """The (manager, security) pairs of one quarter whose implied price is more than 50% from
    the median of the managers holding the security the same way, when at least three do."""
    prices: dict[PriceKey, dict[int, Decimal]] = {}
    for point in points:
        if point.shares > 0 and point.value_usd >= 0:
            prices.setdefault(point.key, {})[point.manager_cik] = point.value_usd / point.shares
    suspects: set[tuple[int, PriceKey]] = set()
    for key, by_manager in prices.items():
        if len(by_manager) < MIN_HOLDERS:
            continue
        median = statistics.median(by_manager.values())
        if median <= 0:
            continue
        for manager_cik, price in by_manager.items():
            if abs(price - median) > median * UNIT_TOLERANCE:
                suspects.add((manager_cik, key))
    return suspects


# --- A parsed report, as stored (E9-S2).


@dataclass(frozen=True)
class F13Record:
    """A 13F-HR or 13F-HR/A parsed from its listing's documents, ready to store."""

    accession_no: str
    manager_cik: int
    form: str  # the listing's: 13F-HR or 13F-HR/A
    filed_at: date
    cover: Cover
    holdings: tuple[Holding, ...]
    unit: UnitRule
    cover_doc: str
    cover_raw_key: str
    table_doc: str | None  # None: the report has no information table
    table_raw_key: str | None

    @property
    def value_sum(self) -> Decimal:
        """The table's values added up, as filed (the summary page states its own total)."""
        return sum((holding.value for holding in self.holdings), Decimal(0))


# --- Positions: amendments, merging and weights (E9-S4).

PositionKey = tuple[str, str | None]  # CUSIP, put or call
WEIGHT_PLACES = Decimal("0.0001")  # percent, to four places
SHARE_TYPES_DIFFER = "share_types_differ"


@dataclass(frozen=True)
class FiledLine:
    """A stored line of a report's information table, values in dollars."""

    accession_no: str
    line_no: int
    cusip: str
    put_call: str | None
    share_type: str
    name_of_issuer: str | None
    title_of_class: str | None
    figi: str | None
    shares: Decimal
    value_as_filed: Decimal
    value_usd: Decimal
    other_managers: str | None

    def repeats(self, other: "FiledLine") -> bool:
        """The same holding as `other`, as filed: a line a NEW HOLDINGS amendment re-lists."""
        return (
            self.cusip,
            self.put_call,
            self.share_type,
            self.shares,
            self.value_as_filed,
            self.other_managers,
        ) == (
            other.cusip,
            other.put_call,
            other.share_type,
            other.shares,
            other.value_as_filed,
            other.other_managers,
        )


@dataclass(frozen=True)
class PeriodFiling:
    """A stored report or amendment of one manager's quarter, with its lines."""

    accession_no: str
    form: str  # 13F-HR or 13F-HR/A
    filed_at: date
    amendment_type: str | None  # RESTATEMENT or NEW HOLDINGS
    lines: tuple[FiledLine, ...]


@dataclass(frozen=True)
class InForce:
    """The lines a quarter's reports leave in force, in the order they were filed."""

    lines: tuple[FiledLine, ...]
    repeated: tuple[tuple[str, int], ...]  # NEW HOLDINGS lines skipped as repeats


def lines_in_force(filings: Sequence[PeriodFiling]) -> InForce:
    """§9.8, in filing order: a report sets the quarter's lines; a RESTATEMENT replaces them
    (FAQ 58: it resubmits the whole report), and so does a later 13F-HR or an amendment that
    names no type; a NEW HOLDINGS amendment adds its lines. A NEW HOLDINGS line that repeats a
    line in force exactly is skipped: ValueAct's amendment for Q3 2024 re-lists the whole
    report with the one holding it adds."""
    lines: list[FiledLine] = []
    repeated: list[tuple[str, int]] = []
    for filing in sorted(filings, key=lambda filing: (filing.filed_at, filing.accession_no)):
        if filing.form == "13F-HR/A" and filing.amendment_type == "NEW HOLDINGS":
            for line in filing.lines:
                if any(line.repeats(earlier) for earlier in lines):
                    repeated.append((line.accession_no, line.line_no))
                else:
                    lines.append(line)
        else:
            lines = list(filing.lines)
    return InForce(tuple(lines), tuple(repeated))


@dataclass(frozen=True)
class Position:
    """A manager's holding of one security one way (shares or principal, put or call) at a
    quarter end: its lines added up."""

    cusip: str
    put_call: str | None
    share_type: str
    name_of_issuer: str | None
    title_of_class: str | None
    figi: str | None
    shares: Decimal
    value_usd: Decimal
    weight_pct: Decimal | None  # of the long equity positions; None for options and principal
    lines: int
    source_accessions: tuple[str, ...]  # the reports its lines come from, in filing order
    quality_flags: tuple[str, ...] = ()

    @property
    def key(self) -> PositionKey:
        return (self.cusip, self.put_call)

    @property
    def is_long_equity(self) -> bool:
        return self.put_call is None and self.share_type == "SH"


def positions(in_force: InForce) -> list[Position]:
    """§9.8: lines merged by CUSIP and put or call (filers split a position across lines, one
    per other manager), largest value first. Weights are of the long equity positions (shares
    held outright); puts, calls and principal amounts have none. Lines of one CUSIP filed as
    both shares and principal keep the type of the larger value, flagged."""
    groups: dict[PositionKey, list[FiledLine]] = {}
    for line in in_force.lines:
        groups.setdefault((line.cusip, line.put_call), []).append(line)
    merged = []
    for (cusip, put_call), lines in groups.items():
        types = {line.share_type for line in lines}
        largest = max(lines, key=lambda line: line.value_usd)
        merged.append(
            Position(
                cusip=cusip,
                put_call=put_call,
                share_type=largest.share_type,
                name_of_issuer=next((ln.name_of_issuer for ln in lines if ln.name_of_issuer), None),
                title_of_class=next((ln.title_of_class for ln in lines if ln.title_of_class), None),
                figi=next((line.figi for line in lines if line.figi), None),
                shares=sum((line.shares for line in lines), Decimal(0)),
                value_usd=sum((line.value_usd for line in lines), Decimal(0)),
                weight_pct=None,
                lines=len(lines),
                source_accessions=tuple(dict.fromkeys(line.accession_no for line in lines)),
                quality_flags=(SHARE_TYPES_DIFFER,) if len(types) > 1 else (),
            )
        )
    total = sum((p.value_usd for p in merged if p.is_long_equity), Decimal(0))
    weighted = [
        replace(p, weight_pct=_weight(p.value_usd, total)) if p.is_long_equity else p
        for p in merged
    ]
    return sorted(weighted, key=lambda p: (-p.value_usd, p.cusip, p.put_call or ""))


def _weight(value: Decimal, total: Decimal) -> Decimal | None:
    if total <= 0:
        return None
    return (value / total * 100).quantize(WEIGHT_PLACES, rounding=ROUND_HALF_EVEN)


# --- Changes from the quarter before (E9-S4), decided when read, with splits.

ChangeType = Literal["new", "added", "trimmed", "unchanged", "exited"]
CHANGE_BAND = Decimal("0.01")  # more than 1% up is added, more than 1% down trimmed (§9.8)
MIN_SPLIT_HOLDERS = 2
MAX_SPLIT = 100
SPLIT_ADJUSTED = "split_adjusted"


@dataclass(frozen=True)
class PositionChange:
    """How a position compares with the same manager's quarter before."""

    key: PositionKey
    change_type: ChangeType
    shares: Decimal  # now; 0 for an exited position
    previous_shares: Decimal | None  # as filed the quarter before; None for a new one
    shares_change: Decimal  # from the previous shares, split-adjusted when there was a split
    split_ratio: Fraction | None  # new shares per old share, when a split is applied


def changes(
    now: Mapping[PositionKey, Decimal],
    before: Mapping[PositionKey, Decimal],
    splits: Mapping[str, Fraction] | None = None,
) -> dict[PositionKey, PositionChange]:
    """§9.8's change types, by shares: `new` (not held the quarter before), `added` (shares up
    more than 1%), `trimmed` (down more than 1%), `unchanged`, `exited` (held the quarter
    before, not now; zero shares). Shares before a split are compared in the new shares."""
    splits = splits or {}
    result: dict[PositionKey, PositionChange] = {}
    for key, shares in now.items():
        previous = before.get(key)
        ratio = splits.get(key[0])
        if previous is None or previous <= 0:
            result[key] = PositionChange(key, "new", shares, previous, shares, None)
            continue
        adjusted = _adjusted(previous, ratio)
        move = (shares - adjusted) / adjusted
        kind: ChangeType = (
            "added" if move > CHANGE_BAND else "trimmed" if move < -CHANGE_BAND else "unchanged"
        )
        result[key] = PositionChange(key, kind, shares, previous, shares - adjusted, ratio)
    for key, previous in before.items():
        if key not in now and previous > 0:
            ratio = splits.get(key[0])
            change = -_adjusted(previous, ratio)
            result[key] = PositionChange(key, "exited", Decimal(0), previous, change, ratio)
    return result


def _adjusted(shares: Decimal, ratio: Fraction | None) -> Decimal:
    if ratio is None:
        return shares
    return shares * ratio.numerator / ratio.denominator


@dataclass(frozen=True)
class HeldBoth:
    """One manager's shares and value of a security at two quarter ends in a row."""

    manager_cik: int
    shares_before: Decimal
    value_before: Decimal
    shares_after: Decimal
    value_after: Decimal


def split_ratio(holders: Sequence[HeldBoth]) -> Fraction | None:
    """A split (or a reverse split) of a security between two quarter ends: at least two
    managers' share counts moved by the same whole ratio (10 to 1 for Netflix in November
    2025, or 1 to 10), and the median price per share moved by its inverse, within a factor of
    two. One manager moving by a whole ratio is a trade."""
    counted: dict[Fraction, int] = {}
    for holder in holders:
        if holder.shares_before <= 0 or holder.shares_after <= 0:
            continue
        ratio = Fraction(holder.shares_after) / Fraction(holder.shares_before)
        whole = ratio.denominator == 1 or ratio.numerator == 1
        if whole and ratio != 1 and Fraction(1, MAX_SPLIT) <= ratio <= MAX_SPLIT:
            counted[ratio] = counted.get(ratio, 0) + 1
    if not counted:
        return None
    ranked = sorted(counted.items(), key=lambda item: -item[1])
    ratio, count = ranked[0]
    if count < MIN_SPLIT_HOLDERS or (len(ranked) > 1 and ranked[1][1] == count):
        return None
    before = _median_price([(h.value_before, h.shares_before) for h in holders])
    after = _median_price([(h.value_after, h.shares_after) for h in holders])
    if before is None or after is None or after == 0:
        return None
    price_move = Fraction(before) / Fraction(after)  # about the ratio after a split
    if not ratio / 2 <= price_move <= ratio * 2:
        return None
    return ratio


def _median_price(points: Sequence[tuple[Decimal, Decimal]]) -> Decimal | None:
    prices = [value / shares for value, shares in points if shares > 0 and value > 0]
    return statistics.median(prices) if prices else None


# --- Value changes of stored positions (E9-S4, D-027).

POSITION_FIELDS = ("shares", "value_usd")


@dataclass(frozen=True)
class ServedPosition:
    """What a stored position serves, for comparison with a quarter built again."""

    cusip: str
    put_call: str | None
    shares: Decimal
    value_usd: Decimal


def position_changes(
    manager_cik: int,
    report_period: date,
    stored: Sequence[ServedPosition],
    built: Sequence[Position],
    reason: Reason,
    detail: str,
) -> list[ValueChange]:
    """The changes a quarter built again makes to its stored positions: shares or value that
    differ, and positions it no longer has (a value to null). A position not stored before is
    not a change: the original report and a NEW HOLDINGS amendment add positions, and either
    can be stored first."""
    now = {position.key: position for position in built}
    found = []
    for old in stored:
        key = (old.cusip, old.put_call)
        new = now.get(key)
        for name in POSITION_FIELDS:
            old_value = getattr(old, name)
            new_value = getattr(new, name) if new is not None else None
            if old_value == new_value:
                continue
            entity_key = {
                "manager_cik": str(manager_cik),
                "report_period": report_period.isoformat(),
                "cusip": old.cusip,
                "field": name,
            }
            if old.put_call is not None:
                entity_key["put_call"] = old.put_call
            found.append(ValueChange(entity_key, old_value, new_value, reason, detail))
    return found


# --- Quarters (E9-S5, S6).

_QUARTER = re.compile(r"^(\d{4})[- ]?Q([1-4])$", re.IGNORECASE)


def quarter_end(day: date) -> date:
    """The last quarter end on or before `day`."""
    end = _end(day.year, (day.month - 1) // 3 * 3 + 3)  # of the quarter `day` is in
    return end if day == end else quarters_before(end, 1)


def quarters_before(period: date, quarters: int) -> date:
    """The quarter end `quarters` quarters before the quarter end `period`."""
    index = period.year * 4 + (period.month - 1) // 3 - quarters
    return _end(index // 4, index % 4 * 3 + 3)


def backfill_start(today: date, quarters: int) -> date:
    """The first filing date a funds backfill of `quarters` quarters reads: the day after the
    quarter end before the oldest of them, so that quarter is loaded too and the oldest one has
    change types."""
    return quarters_before(quarter_end(today), quarters) + timedelta(days=1)


def parse_quarter(text: str) -> date:
    """A quarter as 2026Q2 (or 2026-Q2, 2026 Q2) or as its end date, 2026-06-30."""
    value = text.strip()
    match = _QUARTER.match(value)
    if match:
        return _end(int(match.group(1)), int(match.group(2)) * 3)
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"a quarter is written 2026Q2 or 2026-06-30, got {text!r}") from None
    if quarter_end(day) != day:
        raise ValueError(
            f"{text} is not a quarter end (March 31, June 30, September 30, December 31)"
        )
    return day


def quarter_label(period: date) -> str:
    """2026Q2 for 2026-06-30."""
    return f"{period.year}Q{(period.month - 1) // 3 + 1}"


def _end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])
