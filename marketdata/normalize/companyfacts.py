"""XBRL facts from companyfacts JSON (PLAN.md §8; E4a-S1).

Pure: `parse_companyfacts` takes the bytes of one companyfacts file and returns its us-gaap and
dei facts; ingest/facts.py fetches the file (raw store first) and stores them.

- `facts[taxonomy][concept].units[unit]` is a list of facts, each one value for one period as
  one filing reports it: `start` (absent for instants), `end`, `val`, `accn`, `fy`, `fp`,
  `form`, `filed`, and `frame` on the fact the SEC picked for a calendar period. A period
  appears once for every filing that reports it, so restated values sit side by side and
  §9.4 rule 2 chooses between them. `fy` and `fp` describe the filing, not the period.
- Values are read as Decimal, never float: the JSON writes EPS as 1.01 (§6).
- The CIK is an integer in most companyfacts files, but a zero-padded string in some (SpaceX,
  Figma, CoreWeave) and an unpadded string in others (ExxonMobil Holdings) (EDGE_CASES.md).
- A unit's facts can be an empty object instead of a list (the SEC answers KO's and V's
  NetIncomeLoss with `"USD": {}`): no facts.
- Only us-gaap and dei are loaded (E4a-S1); facts of other taxonomies are counted.
- A fact that isn't in this shape is skipped and counted, never guessed. A file whose top level
  isn't raises SecFileError, and nothing is loaded from it.
- A file can list one key (concept, unit, period, accession) twice: the first is kept, and a
  second with another value is reported as a conflict.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from marketdata.normalize.security_master import SecFileError

TAXONOMIES = ("us-gaap", "dei")

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")

type FactKey = tuple[str, str, str, date | None, date, str]


@dataclass(frozen=True)
class XbrlFact:
    """One value for one period in one filing (an `xbrl_facts` row)."""

    cik: int
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None  # None for an instant
    period_end: date
    value: Decimal
    fy: int | None
    fp: str | None
    form: str | None
    frame: str | None
    filed_at: date
    accession_no: str

    @property
    def key(self) -> FactKey:
        return (
            self.taxonomy,
            self.concept,
            self.unit,
            self.period_start,
            self.period_end,
            self.accession_no,
        )


@dataclass(frozen=True)
class CompanyFacts:
    cik: int
    entity_name: str | None
    facts: tuple[XbrlFact, ...]
    skipped: int = 0  # facts not in the expected shape
    repeated: int = 0  # keys listed again with the same value
    conflicts: tuple[str, ...] = ()  # keys listed again with another value
    other_taxonomies: Mapping[str, int] = field(default_factory=dict)  # facts not loaded

    @property
    def accessions(self) -> frozenset[str]:
        return frozenset(fact.accession_no for fact in self.facts)


def parse_companyfacts(data: bytes) -> CompanyFacts:
    """The us-gaap and dei facts of a companyfacts file."""
    try:
        document = json.loads(data, parse_float=Decimal, parse_constant=_no_constant)
    except ValueError as exc:
        raise SecFileError(f"companyfacts JSON is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise SecFileError("companyfacts JSON: expected an object")
    cik = _cik(document.get("cik"))
    if cik is None:
        raise SecFileError(f"companyfacts JSON has no usable cik: {document.get('cik')!r}")
    taxonomies = document.get("facts", {})
    if not isinstance(taxonomies, dict):
        raise SecFileError(f"companyfacts JSON for CIK {cik}: facts is not an object")
    name = document.get("entityName")
    facts: dict[FactKey, XbrlFact] = {}
    skipped = repeated = 0
    conflicts: list[str] = []
    others: Counter[str] = Counter()
    for taxonomy, concepts in taxonomies.items():
        if not isinstance(concepts, dict):
            raise SecFileError(f"companyfacts JSON for CIK {cik}: {taxonomy} is not an object")
        for concept, entry in concepts.items():
            units = entry.get("units", {}) if isinstance(entry, dict) else None
            if not isinstance(units, dict):
                skipped += 1
                continue
            for unit, rows in units.items():
                if isinstance(rows, dict) and not rows:
                    continue  # "USD": {} lists no facts
                if not isinstance(rows, list):
                    skipped += 1
                    continue
                if taxonomy not in TAXONOMIES:
                    others[taxonomy] += len(rows)
                    continue
                for row in rows:
                    fact = _fact(cik, taxonomy, concept, unit, row)
                    if fact is None:
                        skipped += 1
                        continue
                    kept = facts.setdefault(fact.key, fact)
                    if kept is fact:
                        continue
                    if kept.value == fact.value:
                        repeated += 1
                    else:
                        conflicts.append(
                            f"{taxonomy}:{concept} {unit} {_period(fact)} in "
                            f"{fact.accession_no}: {kept.value} kept, {fact.value} dropped"
                        )
    return CompanyFacts(
        cik=cik,
        entity_name=name.strip() if isinstance(name, str) and name.strip() else None,
        facts=tuple(facts.values()),
        skipped=skipped,
        repeated=repeated,
        conflicts=tuple(conflicts),
        other_taxonomies=dict(others),
    )


def _fact(cik: int, taxonomy: str, concept: str, unit: str, row: Any) -> XbrlFact | None:
    if not isinstance(row, dict):
        return None
    end, start, filed = _date(row.get("end")), _date(row.get("start")), _date(row.get("filed"))
    value, accession = row.get("val"), row.get("accn")
    if end is None or filed is None or ("start" in row and start is None):
        return None
    if start is not None and start > end:
        return None
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    if not (isinstance(accession, str) and _ACCESSION.match(accession)):
        return None
    fy, fp, form, frame = row.get("fy"), row.get("fp"), row.get("form"), row.get("frame")
    return XbrlFact(
        cik=cik,
        taxonomy=taxonomy,
        concept=concept,
        unit=unit,
        period_start=start,
        period_end=end,
        value=Decimal(value),
        fy=fy if isinstance(fy, int) and not isinstance(fy, bool) else None,
        fp=fp if isinstance(fp, str) and fp else None,
        form=form if isinstance(form, str) and form else None,
        frame=frame if isinstance(frame, str) and frame else None,
        filed_at=filed,
        accession_no=accession,
    )


def _cik(value: Any) -> int | None:
    """An integer for most companies, but a zero-padded string for some (SpaceX: "0001181412")
    and an unpadded one for others (ExxonMobil Holdings: "2115436")."""
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _period(fact: XbrlFact) -> str:
    end = fact.period_end.isoformat()
    return end if fact.period_start is None else f"{fact.period_start.isoformat()} to {end}"


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a number")
