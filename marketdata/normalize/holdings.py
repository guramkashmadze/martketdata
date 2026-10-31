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

import statistics
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, cast

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
