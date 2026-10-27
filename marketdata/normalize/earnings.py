"""From an extraction to verified earnings rows (E7a-S4, PLAN.md §9.9).

Pure. `assemble` checks every value an extractor claims with verify/ (in the stored text,
scale, sanity) and builds the `earnings_releases` and `earnings_facts` rows:

- A slot whose values pass every check is `passed` and served; one that fails any check is
  `failed` and stored as null with its reason (the extracted values stay in the check report).
- The change from the prior-year quarter: (value − prior) / |prior| × 100, rounded to one
  decimal; null without a prior value, when it is zero, or when the sign changes (flag
  `sign_change`).
- The release is `complete` when every slot that applies passed, `partial` when some did,
  `failed` when none did, and `no_statement` when no exhibit has an income line at all
  (D-071): an 8-K with item 2.02 that is no earnings release.

Banks (SIC 6000-6099, deposit-taking institutions) have no revenue slot (D-072); insurers,
REITs and lenders keep it because their releases print total revenues.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from marketdata.extract.earnings_rules import Override
from marketdata.extract.interface import USD, ExtractionResult, Figure, Slot, SlotResult
from marketdata.normalize.periods import fiscal_quarter
from marketdata.verify.number_check import (
    IN_SOURCE,
    SCALE,
    Claim,
    Problem,
    check_claim,
    scale_problem,
)
from marketdata.verify.sanity import SANITY, change_problem, eps_problem, revenue_problem

type CheckStatus = Literal["passed", "failed", "not_found", "not_applicable"]
type ReleaseStatus = Literal["complete", "partial", "failed", "no_statement"]

BANK_SIC = range(6000, 6100)
BANK_REASON = "banks (SIC 6000-6099): revenue is not applicable (PLAN.md §9.9, D-072)"

# Quality flags (§9.5) of earnings facts.
SCALE_UNKNOWN = "scale_unknown"
SIGN_CHANGE = "sign_change"
NOT_IN_SOURCE = "not_in_source"
SCALE_MISMATCH = "scale_mismatch"
CHANGE_TOO_LARGE = "change_over_1000_pct"
REVENUE_NOT_POSITIVE = "revenue_not_positive"
EPS_MISMATCH = "eps_net_income_mismatch"
_PROBLEM_FLAGS = {IN_SOURCE: NOT_IN_SOURCE, SCALE: SCALE_MISMATCH}
CHECKS = (IN_SOURCE, SCALE, SANITY)


def revenue_not_applicable(sic_code: str | None) -> str | None:
    """Why a company has no revenue slot, or None when it has one."""
    if sic_code is not None and sic_code.isdigit() and int(sic_code) in BANK_SIC:
        return BANK_REASON
    return None


def not_applicable_slots(
    cik: int, accession_no: str, sic_code: str | None, overrides: Sequence[Override]
) -> tuple[dict[Slot, str], list[str]]:
    """The slots that don't apply to a release, each with why: revenue for a bank, and each slot
    a `not_applicable` override names for the company (and for this release, when it names
    one). Also the overrides used, as described."""
    reasons: dict[Slot, str] = {}
    bank = revenue_not_applicable(sic_code)
    if bank is not None:
        reasons["revenue"] = bank
    used = []
    for override in overrides:
        if (
            override.cik == cik
            and override.not_applicable is not None
            and override.accession_no in (None, accession_no)
        ):
            reasons[override.slot] = f"override: {override.not_applicable}"
            used.append(override.describe())
    return reasons, used


@dataclass(frozen=True)
class Change:
    pct: Decimal | None
    flags: tuple[str, ...] = ()


def change(value: Decimal, prior: Decimal | None) -> Change:
    if prior is None or prior == 0:
        return Change(None)
    if (value < 0 < prior) or (prior < 0 < value):
        return Change(None, (SIGN_CHANGE,))
    pct = ((value - prior) / abs(prior) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return Change(pct)


@dataclass(frozen=True)
class FactRecord:
    """An earnings_facts row."""

    slot: Slot
    unit: str
    method: str
    extractor_version: str
    check_status: CheckStatus
    value: Decimal | None = None
    prior_value: Decimal | None = None
    change_pct: Decimal | None = None
    source_row_label: str | None = None
    source_quote: str | None = None
    scale_text: str | None = None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseRecord:
    """An earnings_releases row and its facts."""

    accession_no: str
    cik: int
    accepted_at: datetime | None
    doc_name: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    period_end: date | None
    period_label: str | None
    status: ReleaseStatus
    extractor: str
    extractor_version: str
    check_report: dict[str, Any]
    facts: tuple[FactRecord, ...] = field(default_factory=tuple)

    def fact(self, slot: Slot) -> FactRecord:
        return next(fact for fact in self.facts if fact.slot == slot)


def assemble(
    result: ExtractionResult,
    texts: Mapping[str, str],
    *,
    accession_no: str,
    cik: int,
    accepted_at: datetime | None,
    fiscal_year_end: str | None,
    report: Mapping[str, Any] | None = None,
) -> ReleaseRecord:
    """Check `result` against the stored texts (exhibit name to `documents.text`) and build the
    rows. `report` adds to the check report (exhibits, acceptance times)."""
    support, support_notes = _supporting(result, texts)
    net_income = _passed_value(result.slot("net_income"), texts)
    facts = []
    slots_report: dict[str, Any] = {}
    for slot_result in result.slots:
        fact, entry = _fact(slot_result, texts, result.extractor_version, support, net_income)
        facts.append(fact)
        slots_report[slot_result.slot] = entry
    status = release_status([fact.check_status for fact in facts], result.income_lines_found)
    labels = fiscal_quarter(result.period_end, fiscal_year_end) if result.period_end else None
    check_report: dict[str, Any] = {
        "extractor": result.extractor,
        "extractor_version": result.extractor_version,
        "statement": {
            "found": result.statement_found,
            "income_lines_found": result.income_lines_found,
            "doc_name": result.doc_name,
            "table_index": result.table_index,
            "period_label": result.period_label,
            "prior_period_end": _iso(result.prior_period_end),
        },
        "slots": slots_report,
        "notes": [*result.notes, *support_notes],
        "overrides": list(result.overrides),
        **(report or {}),
    }
    return ReleaseRecord(
        accession_no=accession_no,
        cik=cik,
        accepted_at=accepted_at,
        doc_name=result.doc_name,
        fiscal_year=labels.fiscal_year if labels else None,
        fiscal_period=labels.fiscal_period if labels else None,
        period_end=result.period_end,
        period_label=result.period_label,
        status=status,
        extractor=result.extractor,
        extractor_version=result.extractor_version,
        check_report=check_report,
        facts=tuple(facts),
    )


def release_status(statuses: Iterable[CheckStatus], income_lines_found: bool) -> ReleaseStatus:
    checked = [status for status in statuses if status != "not_applicable"]
    if not income_lines_found:
        return "no_statement"
    passed = sum(1 for status in checked if status == "passed")
    if checked and passed == len(checked):
        return "complete"
    return "partial" if passed else "failed"


@dataclass(frozen=True)
class _Support:
    shares: tuple[Decimal, ...]  # readings of the diluted share count (scaled, as printed)
    to_common: Decimal | None


def _supporting(result: ExtractionResult, texts: Mapping[str, str]) -> tuple[_Support, list[str]]:
    """The share count and net income to common for the EPS check, each checked first."""
    notes: list[str] = []
    checked: list[Figure | None] = []
    for name, figure in (
        ("diluted shares", result.diluted_shares),
        ("net income to common", result.net_income_to_common),
    ):
        if figure is None:
            checked.append(None)
            continue
        problems = check_claim(_claim(name, figure), texts.get(figure.doc_name, ""))
        if problems:
            notes.extend(f"not used for the EPS check: {problem.message}" for problem in problems)
            checked.append(None)
        else:
            checked.append(figure)
    shares, to_common = checked
    readings = (shares.value, shares.value / shares.scale) if shares else ()
    return _Support(tuple(dict.fromkeys(readings)), to_common.value if to_common else None), notes


def _passed_value(slot: SlotResult, texts: Mapping[str, str]) -> Decimal | None:
    if slot.outcome != "found" or slot.current is None:
        return None
    if check_claim(_claim(slot.slot, slot.current), texts.get(slot.current.doc_name, "")):
        return None
    return slot.current.value


def _fact(
    slot: SlotResult,
    texts: Mapping[str, str],
    version: str,
    support: _Support,
    net_income: Decimal | None,
) -> tuple[FactRecord, dict[str, Any]]:
    base = FactRecord(slot.slot, slot.unit, slot.method, version, "not_found")
    entry: dict[str, Any] = {"outcome": slot.outcome, "note": slot.note}
    if slot.outcome == "not_applicable":
        return _with(base, check_status="not_applicable"), entry
    if slot.outcome == "not_found":
        return base, entry
    if slot.outcome == "failed" or slot.current is None:
        return _with(base, check_status="failed", quality_flags=slot.flags), {
            **entry,
            "flags": list(slot.flags),
        }
    current, prior = slot.current, slot.prior
    entry.update(
        {
            "value": str(current.value),
            "cell": current.cell,
            "prior_value": str(prior.value) if prior else None,
            "prior_cell": prior.cell if prior else None,
            "doc_name": current.doc_name,
        }
    )
    problems: list[Problem] = []
    for name, figure in ((slot.slot, current), (f"{slot.slot} (prior year)", prior)):
        if figure is not None:
            problems += check_claim(_claim(name, figure), texts.get(figure.doc_name, ""))
    stated = scale_problem(
        current.scale,
        slot.scale_text,
        texts.get(current.doc_name, ""),
        per_share=slot.unit != USD,
    )
    if stated is not None:
        problems.append(stated)
    sanity = _sanity(slot, current.value, prior.value if prior else None, support, net_income)
    problems += [Problem(SANITY, message) for message, _ in sanity]
    flags = [_PROBLEM_FLAGS[p.check] for p in problems if p.check in _PROBLEM_FLAGS]
    flags += [flag for _, flag in sanity]
    if problems:
        entry["problems"] = [problem.message for problem in problems]
        failed = _with(
            base,
            check_status="failed",
            source_row_label=current.row_label,
            source_quote=current.quote,
            scale_text=slot.scale_text,
            quality_flags=tuple(dict.fromkeys(flags)),
        )
        return failed, entry
    moved = change(current.value, prior.value if prior else None)
    entry["checks"] = list(CHECKS)
    passed = _with(
        base,
        check_status="passed",
        value=current.value,
        prior_value=prior.value if prior else None,
        change_pct=moved.pct,
        source_row_label=current.row_label,
        source_quote=current.quote,
        scale_text=slot.scale_text,
        quality_flags=moved.flags,
    )
    return passed, entry


def _sanity(
    slot: SlotResult,
    value: Decimal,
    prior: Decimal | None,
    support: _Support,
    net_income: Decimal | None,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    moved = change_problem(value, prior)
    if moved:
        found.append((moved, CHANGE_TOO_LARGE))
    if slot.slot == "revenue":
        negative = revenue_problem(value)
        if negative:
            found.append((negative, REVENUE_NOT_POSITIVE))
    if slot.slot == "eps_diluted":
        mismatch = eps_problem(value, support.shares, net_income, support.to_common)
        if mismatch:
            found.append((mismatch, EPS_MISMATCH))
    return found


def _claim(name: str, figure: Figure) -> Claim:
    return Claim(name, figure.value, figure.cell, figure.scale, figure.quote, figure.doc_name)


def _with(fact: FactRecord, **changes: Any) -> FactRecord:
    values = {**fact.__dict__, **changes}
    return FactRecord(**values)


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def facts_by_slot(facts: Sequence[FactRecord]) -> dict[Slot, FactRecord]:
    return {fact.slot: fact for fact in facts}
