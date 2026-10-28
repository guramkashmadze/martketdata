"""Decides the reason for a stored value change (D-027; E7a onward).

Pure. Stored values never change silently: when a run gives a served value that differs from
the one stored, a `value_changes` row records the old value, the new value and why (D-077):

- `correction`: the same release run again, with new rules (RULES_VERSION), a new override,
  or new document text rules; the detail says which version changed.
- `amendment`: an 8-K/A for the same company and quarter as a release already stored; its
  values are compared with that release's (the original stays stored).
- `restatement` (financial metrics, E4a): a filing filed after the one a stored metric came
  from reports the period with another value (§9.4 rule 2: the latest filing wins).
- `correction` (financial metrics): the calc version changed, or the stored facts did without
  a later filing (the SEC dropped a filing's facts); the detail says which.

A served value is what the API shows: the value of a slot that passed, else null. Both the
current value and the prior-year value are compared; a slot that newly passes or newly fails
is a change too (null to a value, or a value to null).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from marketdata.normalize.metrics import MetricKey, MetricRow

type Reason = Literal["correction", "restatement", "amendment"]
FIELDS = ("value", "prior_value")


@dataclass(frozen=True)
class Served:
    """What a slot of a stored release serves."""

    slot: str
    value: Decimal | None
    prior_value: Decimal | None
    extractor_version: str


@dataclass(frozen=True)
class ValueChange:
    entity_key: Mapping[str, str]  # accession_no, slot, field (and the amended release)
    old_value: Decimal | None
    new_value: Decimal | None
    reason: Reason
    detail: str


def correction_changes(
    accession_no: str, stored: Sequence[Served], new: Sequence[Served]
) -> list[ValueChange]:
    """The changes a re-run of one release makes to its own stored values."""
    old_versions = sorted({row.extractor_version for row in stored})
    new_versions = sorted({row.extractor_version for row in new})
    if old_versions != new_versions:
        detail = f"rules version {', '.join(old_versions)} to {', '.join(new_versions)}"
    else:
        detail = (
            f"rules version {', '.join(new_versions)} unchanged: an override, the exhibit "
            "text rules or the company's details changed"
        )
    return [
        ValueChange(key, old, new_value, "correction", detail)
        for key, old, new_value in _differences(accession_no, stored, new)
    ]


def amendment_changes(
    accession_no: str, amended: str, original: Sequence[Served], new: Sequence[Served]
) -> list[ValueChange]:
    """The changes an 8-K/A makes to the values of the release it amends."""
    detail = f"8-K/A {accession_no} amends the earnings release {amended}"
    return [
        ValueChange({**key, "amends": amended}, old, new_value, "amendment", detail)
        for key, old, new_value in _differences(accession_no, original, new)
    ]


def _differences(
    accession_no: str, old: Sequence[Served], new: Sequence[Served]
) -> list[tuple[dict[str, str], Decimal | None, Decimal | None]]:
    before = {row.slot: row for row in old}
    changes = []
    for row in new:
        previous = before.get(row.slot)
        if previous is None:
            continue
        for name in FIELDS:
            old_value, new_value = getattr(previous, name), getattr(row, name)
            if old_value != new_value:
                key = {"accession_no": accession_no, "slot": row.slot, "field": name}
                changes.append((key, old_value, new_value))
    return changes


@dataclass(frozen=True)
class StoredMetric:
    """What a stored `financial_metrics` row serves, and where it came from."""

    value: Decimal | None
    accession_no: str | None
    filed_at: date | None
    calc_version: str


def metric_changes(
    stored: Mapping[MetricKey, StoredMetric], new: Sequence[MetricRow]
) -> list[ValueChange]:
    """The changes a rebuild of one company's metrics makes to its stored values (D-027)."""
    changes = []
    for row in new:
        old = stored.get(row.key)
        if old is None or old.value == row.value:
            continue
        key = {
            "metric": row.metric,
            "period_type": row.period_type,
            "period_end": row.period_end.isoformat(),
        }
        if (
            row.accession_no != old.accession_no
            and row.filed_at is not None
            and old.filed_at is not None
            and row.filed_at > old.filed_at
        ):
            reason: Reason = "restatement"
            detail = (
                f"{row.accession_no}, filed {row.filed_at}, restates {old.accession_no} "
                f"of {old.filed_at}"
            )
        elif old.calc_version != row.calc_version:
            reason, detail = "correction", f"calc version {old.calc_version} to {row.calc_version}"
        else:
            reason = "correction"
            detail = (
                f"calc version {row.calc_version} unchanged: the stored facts changed "
                f"({old.accession_no} to {row.accession_no})"
            )
        changes.append(ValueChange(key, old.value, row.value, reason, detail))
    return changes
