"""Extractor protocol and ExtractionResult (E7a-S2, D-015).

An extractor reads the exhibits of one earnings release and returns, for each slot, the values
it found and where: the document, the row label and the cells as printed, the scale it applied,
its method and version. It never judges its own output. verify/ checks every value against the
stored document text, whoever extracted it, so a later LLM extractor (E12) plugs in here and
goes through the same checks.

Values are in base units: USD, or USD per share for EPS. A dash printed for nil reads as zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from marketdata.extract.html_tables import Table

type Slot = Literal["revenue", "operating_income", "net_income", "eps_diluted"]
SLOTS: tuple[Slot, ...] = ("revenue", "operating_income", "net_income", "eps_diluted")

# found: the extractor has values for verification to check; not_found: no row matched;
# not_applicable: the slot doesn't apply (a bank's revenue, §9.9); failed: a row matched but
# its values can't be read (no scale, no quarter column), with flags saying why.
type Outcome = Literal["found", "not_found", "not_applicable", "failed"]

USD = "USD"
USD_PER_SHARE = "USD/shares"
SHARES = "shares"
SLOT_UNITS: Mapping[Slot, str] = {
    "revenue": USD,
    "operating_income": USD,
    "net_income": USD,
    "eps_diluted": USD_PER_SHARE,
}


@dataclass(frozen=True)
class ReleaseDocument:
    """An exhibit of the filing as stored: its text and the tables that text was written from."""

    doc_name: str
    doc_type: str  # EX-99.1, EX-99, EX-99.2 …
    text: str  # documents.text
    tables: tuple[Table, ...]


@dataclass(frozen=True)
class Release:
    """What an extractor reads: one filing's exhibits, in the order they are tried (§9.9)."""

    cik: int
    accession_no: str
    form: str  # 8-K or 8-K/A
    documents: tuple[ReleaseDocument, ...]
    # Slots that don't apply to this company, with the reason: a bank's revenue (§9.9) or an
    # override for a line the company doesn't present.
    not_applicable: Mapping[Slot, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Figure:
    """One printed number and where it is."""

    value: Decimal  # the cell's number times the scale, in base units
    cell: str  # as printed, e.g. "$ 29,789" or "(171)"
    scale: Decimal  # what the printed number was multiplied by; 1 for per-share values
    doc_name: str
    row_label: str  # as printed, footnote markers included
    quote: str  # the whole row as printed: a line of the document's text


@dataclass(frozen=True)
class SlotResult:
    slot: Slot
    outcome: Outcome
    unit: str
    method: str  # 'rule' or 'llm', stored with each value
    current: Figure | None = None  # the current quarter
    prior: Figure | None = None  # the same quarter a year earlier, from the same row
    scale_text: str | None = None  # e.g. "In millions, except per-share amounts"
    flags: tuple[str, ...] = ()  # e.g. scale_unknown
    note: str | None = None  # why it wasn't found or failed, or the override used


@dataclass(frozen=True)
class ExtractionResult:
    extractor: str  # 'rules' (E7a), 'llm' (E12)
    extractor_version: str
    slots: tuple[SlotResult, ...]  # one per slot, in SLOTS order
    statement_found: bool  # an exhibit had an income statement table
    doc_name: str | None = None  # the exhibit the values came from
    table_index: int | None = None
    period_end: date | None = None  # the current quarter column's end date
    prior_period_end: date | None = None
    period_label: str | None = None  # the current column's headings as printed
    diluted_shares: Figure | None = None  # for the EPS sanity check (§9.9)
    net_income_to_common: Figure | None = None  # likewise, when the table prints it
    notes: tuple[str, ...] = ()  # what the extractor saw, kept in the check report
    overrides: tuple[str, ...] = ()  # overrides applied, counted in the hit-rate report

    def slot(self, slot: Slot) -> SlotResult:
        return next(result for result in self.slots if result.slot == slot)


class Extractor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def extract(self, release: Release) -> ExtractionResult: ...
