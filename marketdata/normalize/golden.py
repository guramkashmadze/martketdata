"""The golden sets as values (PLAN.md §6, §9.10; E5b-S4): read by hand from earnings releases,
each with the SEC document it was read from. tests/golden/ keeps them as TOML (D-078, D-093);
the accuracy report compares them with what the platform serves.

Pure: a file's text in, golden values out, read as the golden tests read them.

- Earnings (`earnings/*.toml`): each release's four slots as printed, the quarter and the same
  quarter a year earlier, times the release's scale; per-share values aren't scaled. A slot the
  release doesn't present is not applicable; one under `differences` is a known miss (the value
  isn't served, e.g. above the ±1000% sanity limit). A release without a statement has none.
- Metrics (`metrics/*.toml`): each period's values as the releases print them, money and share
  counts in millions, per-share values and percentages as printed. `known_differences` names
  values the filings give otherwise (or not at all); `not_applicable` those that don't apply.
"""

import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from marketdata.extract.interface import SLOTS
from marketdata.normalize.metric_catalog import UNITS
from marketdata.verify.number_check import read_number

type Kind = Literal["earnings", "metric"]
type Status = Literal["expected", "known_difference", "not_applicable"]

SCALES = {"thousands": Decimal(1000), "millions": Decimal(1_000_000)}
MILLION = Decimal(1_000_000)
PERIOD_KEYS = frozenset(
    {"type", "period_end", "fiscal", "url", "not_applicable", "known_differences"}
)


class GoldenFileError(ValueError):
    """A golden file that can't be read; the message names the file and the entry."""


@dataclass(frozen=True)
class GoldenValue:
    kind: Kind
    cik: int
    accession_no: str | None  # earnings: the release
    slot: str | None  # earnings
    metric: str | None  # metrics
    period_type: str | None
    period_end: date | None
    value: Decimal | None  # as printed, in base units; None when it doesn't apply
    prior_value: Decimal | None  # earnings: the same quarter a year earlier
    status: Status
    note: str | None  # why a known difference differs
    source_url: str
    source_file: str


def earnings_values(text: str, source_file: str) -> list[GoldenValue]:
    """The slots of each release a golden earnings file holds."""
    company = _toml(text, source_file)
    values = []
    for release in company.get("release", []):
        if release.get("status") == "no_statement":
            continue
        accession = str(release["accession_no"])
        where = f"{source_file}, release {accession}"
        not_applicable = set(release.get("not_applicable", []))
        differences: dict[str, str] = release.get("differences", {})
        scale = SCALES.get(str(release.get("scale")))
        for slot in SLOTS:
            common: dict[str, Any] = {
                "kind": "earnings",
                "cik": int(company["cik"]),
                "accession_no": accession,
                "slot": slot,
                "metric": None,
                "period_type": None,
                "period_end": None,
                "source_url": str(release["url"]),
                "source_file": source_file,
            }
            if slot in not_applicable:
                values.append(
                    GoldenValue(
                        **common, value=None, prior_value=None, status="not_applicable", note=None
                    )
                )
                continue
            if slot not in release:
                raise GoldenFileError(f"{where}: {slot} has no printed values")
            if scale is None and slot != "eps_diluted":
                raise GoldenFileError(f"{where}: scale must be thousands or millions")
            factor = Decimal(1) if slot == "eps_diluted" else scale or Decimal(1)
            printed, prior = release[slot]
            values.append(
                GoldenValue(
                    **common,
                    value=_printed(printed, where) * factor,
                    prior_value=_printed(prior, where) * factor,
                    status="known_difference" if slot in differences else "expected",
                    note=differences.get(slot),
                )
            )
    return values


def metric_values(text: str, source_file: str) -> list[GoldenValue]:
    """The values of each period a golden metrics file holds."""
    company = _toml(text, source_file)
    values = []
    for period in company.get("period", []):
        end = period["period_end"]
        if not isinstance(end, date):
            raise GoldenFileError(f"{source_file}: period_end {end!r} is not a date")
        known: dict[str, str] = period.get("known_differences", {})
        common: dict[str, Any] = {
            "kind": "metric",
            "cik": int(company["cik"]),
            "accession_no": None,
            "slot": None,
            "period_type": str(period["type"]),
            "period_end": end,
            "prior_value": None,
            "source_url": str(period["url"][0]),
            "source_file": source_file,
        }
        for metric in sorted(set(period) - PERIOD_KEYS):
            if metric not in UNITS:
                raise GoldenFileError(f"{source_file}, {end}: unknown metric {metric}")
            values.append(
                GoldenValue(
                    **common,
                    metric=metric,
                    value=_metric_value(metric, str(period[metric])),
                    status="known_difference" if metric in known else "expected",
                    note=known.get(metric),
                )
            )
        for metric in period.get("not_applicable", []):
            values.append(
                GoldenValue(**common, metric=metric, value=None, status="not_applicable", note=None)
            )
    return values


def _toml(text: str, source_file: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise GoldenFileError(f"{source_file}: {exc}") from None


def _printed(text: str, where: str) -> Decimal:
    number = read_number(text)
    if number is None:
        raise GoldenFileError(f"{where}: {text!r} is not a printed number")
    return number


def _metric_value(metric: str, printed: str) -> Decimal:
    """Money and share counts are given in millions; per-share values and percentages as
    printed (tests/golden/metrics/README.md)."""
    negative = printed.startswith("(")
    number = Decimal(printed.strip("()").replace(",", ""))
    if UNITS[metric] in ("USD", "shares"):
        number *= MILLION
    return -number if negative else number
