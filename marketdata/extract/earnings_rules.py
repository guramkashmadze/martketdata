"""Rule-based earnings extractor and its label patterns, versioned with RULES_VERSION (E7a-S3,
PLAN.md §9.9).

Pure. For one release:

1. The statement table: in the exhibits in the order they are tried, a table with a current
   and a prior-year quarter column (columns.py) whose rows have a revenue line (unless revenue
   doesn't apply) and a net income or diluted EPS line. A table titled as the statement of
   operations, income or earnings wins over one that only has those lines (a summary above it);
   a table titled non-GAAP, adjusted or a reconciliation is never the statement. A table right
   after it without a title of its own continues it (a statement split over two tables).
2. The rows: each slot's label patterns, best first. An unlabeled row of values closes the
   section above it ("Revenues" … "| | 3,583 |" is total revenues, Shopify). "Diluted" rows
   are EPS under an "… per share" heading and share counts under a "shares" heading (Apple).
   The amount attributable to the company wins over the amount including noncontrolling
   interests.
3. The values: the printed cells of the current and prior-year columns, times the scale the
   table states (units.py); per-share values are not scaled. No scale: `scale_unknown`.

Nothing here checks the values; verify/ does that against the stored text (D-015).
"""

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from marketdata.extract.columns import (
    Column,
    Periods,
    heading_date,
    heading_rows,
    table_periods,
    title_lines,
)
from marketdata.extract.html_tables import Row, Table
from marketdata.extract.interface import (
    SHARES,
    SLOT_UNITS,
    SLOTS,
    USD,
    ExtractionResult,
    Figure,
    Release,
    ReleaseDocument,
    Slot,
    SlotResult,
)
from marketdata.extract.units import Scale, find_scale
from marketdata.normalize.periods import fiscal_quarter

RULES_VERSION = "1"
METHOD = "rule"

# --- Labels -----------------------------------------------------------------------------------

_COMPANY = r"(?!non-?controlling|minority|redeemable|participating|preferred|common|class\b)"
# Each slot's patterns, best first; a label must match one in full (after _normal).
REVENUE = (
    r"total (?:net )?revenues?",
    r"total net sales(?: and revenues?)?",
    r"total (?:net )?operating revenues?",
    r"total revenues? and other income",
    r"(?:net )?revenues?",
    r"net sales(?: and revenues?)?",
    r"revenues?(?:,| -)? net",
    r"(?:net )?operating revenues?",
    r"sales to customers",
    r"total sales",
    r"sales",
    r"total revenues?,? net",
)
OPERATING_INCOME = (
    r"(?:total )?operating income(?: \(loss\))?",
    r"operating (?:income )?\(loss\)(?: income)?",
    r"operating loss",
    r"(?:income|earnings) \(loss\) from operations",
    r"\(loss\) income from operations",
    r"(?:income|earnings) from operations",
    r"loss from operations",
    r"operating profit(?: \(loss\))?",
)
NET_INCOME = (
    r"(?:consolidated )?net (?:income|earnings)(?: \(loss\))? attributable to " + _COMPANY + r".+",
    r"net \(loss\) (?:income|earnings) attributable to " + _COMPANY + r".+",
    r"net loss attributable to " + _COMPANY + r".+",
    r"(?:consolidated )?net (?:income|earnings)(?: \(loss\))?",
    r"net \(loss\) (?:income|earnings)",
    r"net loss(?: \(income\))?",
    r"net (?:income|earnings|loss)(?: \(loss\))? (?:available|applicable|attributable) to common "
    r"(?:stock|share)holders",
    r"net (?:income|earnings)(?: \(loss\))? (?:available|applicable) to common (?:equity|stock)",
    r"net (?:\(loss\) )?(?:income|earnings|loss)(?: \(loss\))? and comprehensive "
    r"(?:\(loss\) )?(?:income|loss)(?: \(loss\))?",
)
# Rows of the attributable-to-common kind rank above plain net income only when the table has
# a noncontrolling-interest line: then the plain line includes the minority's share.
_TO_COMMON = re.compile(r"\bto common\b")
_NONCONTROLLING = re.compile(r"non-?controlling|minority interest")
_SUBJECT = (
    r"(?:net )?(?:earnings|income|loss|\(loss\) earnings|earnings \(loss\)|income \(loss\)"
    r"|\(loss\) income|loss \(income\))(?: \(loss\))?"
)
_OF_WHOM = r"(?: attributable to .+)?"
EPS = (
    rf"diluted {_SUBJECT} per (?:common )?share{_OF_WHOM}",
    rf"{_SUBJECT} per (?:common )?share{_OF_WHOM},? (?:- )?(?:basic and )?"
    r"(?:diluted|assuming dilution)",
    rf"{_SUBJECT} per (?:common )?share \((?:basic and )?(?:diluted|assuming dilution)\)",
    rf"{_SUBJECT} per diluted (?:common )?share{_OF_WHOM}",
    r"diluted eps",
    r"eps,? (?:- )?diluted",
    rf"basic and diluted {_SUBJECT} per (?:common )?share{_OF_WHOM}",
    rf"{_SUBJECT} per (?:common )?share,? basic and diluted{_OF_WHOM}",
)
# In a section headed "Earnings per share:", the diluted line is labelled only "Diluted" (Apple);
# in one headed "Diluted earnings per share" (or "… per share > Assuming dilution", IBM) the
# line is the total, or one per class of stock (Visa: Class A first).
DILUTED_IN_SECTION = (r"diluted", r"basic and diluted", r"diluted earnings", r"assuming dilution")
TOTAL_IN_SECTION = (r"total(?: .+)?", rf"{_SUBJECT}", r"(?:net )?earnings per share")
_CLASS = re.compile(r"^class (?P<letter>[a-z])\b")
SHARES_DILUTED = (
    r"(?:weighted[- ]average )?(?:number of )?(?:common )?shares (?:outstanding )?"
    r"(?:used in computing |used in )?.*(?:diluted|dilution).*",
    r"average - assuming dilution",
    r"diluted (?:weighted[- ]average )?(?:common )?shares(?: outstanding)?",
    r"weighted[- ]average diluted (?:common )?shares(?: outstanding)?",
    r"average diluted shares(?: outstanding)?",
)
# The numerator of diluted EPS when the table prints it: the "diluted" line first (IonQ adjusts
# it for warrant gains), then the amount to common shareholders (JPMorgan's is after preferred
# dividends).
NET_INCOME_TO_COMMON = (
    rf"{_SUBJECT} (?:available|applicable|attributable) to common (?:stock|share)holders?"
    r",? (?:- )?(?:basic and )?diluted",
    rf"{_SUBJECT} (?:available|applicable|attributable) to common shares,? (?:- )?diluted",
    rf"{_SUBJECT} (?:available|applicable|attributable) to common (?:stock|share)holders",
    rf"{_SUBJECT} (?:available|applicable) to common (?:equity|stock)",
)
# Never a slot's line, whatever else the label says.
_EXCLUDED = re.compile(
    r"non-?gaap|adjusted|excluding|\bexcl\.|margin|%|percent|growth|\bper cent\b|comprehensive"
    r"|\bpro forma\b|\bconstant currency\b|\borganic\b|\bcost of\b|\bdeferred\b|\bunearned\b",
)
_PER_SHARE = re.compile(r"\bper\b.*\bshare|\beps\b|\bper diluted\b")
_SHARE_COUNT = re.compile(
    r"\bweighted\b|\baverage (?:number of )?(?:common )?shares\b|\bshares (?:outstanding|used)\b"
    r"|\bnumber of (?:common )?shares\b|^(?:diluted )?shares\b|\bshares used\b"
)
_PER_SHARE_ONLY = re.compile(r"^per (?:diluted |basic )?(?:common )?share\b")
_CONTINUING = re.compile(r"continuing operations|discontinued")

# --- Tables -----------------------------------------------------------------------------------

_STATEMENT_TITLE = re.compile(
    r"statements? of (?:consolidated )?(?:operations|income|earnings|comprehensive income)"
    r"|income statements?|statements? of (?:operations|income) data|results of operations",
    re.IGNORECASE,
)
_NOT_STATEMENT = re.compile(
    r"non-?gaap|adjusted|reconciliation|segment|selected (?:financial|business)|supplemental"
    r"|\bas a percent(?:age)? of\b|\bpercent(?:age)? of (?:total )?(?:net )?(?:revenues?|sales)\b",
    re.IGNORECASE,
)
_CONTINUED = re.compile(r"\bcontinued\b|\(cont", re.IGNORECASE)
TITLE_LINE = 200  # a context line longer than this is a paragraph, not a title
RELEASE_LAG = timedelta(days=130)  # a release reports a quarter that ended this long before
UNNAMED_LAG = timedelta(days=100)  # nearer than the end of the quarter before

_QUARTER_ENDED = re.compile(
    r"(?:three|3)[\s-]+months?\s+ended|quarter(?:\s+of\s+(?:fiscal\s+)?(?:year\s+)?\d{4})?,?"
    r"\s+ended|(?:twelve|thirteen|fourteen|sixteen|12|13|14|16)[\s-]+weeks?\s+ended",
    re.IGNORECASE,
)

# --- Numbers ----------------------------------------------------------------------------------

_NIL = re.compile(r"^[—–‒―−-]+$")
# A footnote marker after the number: "2.31 (1)", "0.75*". "(89)" alone is a negative number.
_FOOTNOTE_SUFFIX = re.compile(
    r"(?<=[\d)])(?:\s+\(\s*(?:[a-z]{1,2}|\d)\s*\)|\s*\*+|\s*[†‡§]+)$", re.IGNORECASE
)
_SECTION_EXCLUDED = re.compile(r"non-?gaap|adjusted|excluding")


class OverrideError(ValueError):
    """The overrides file has problems; each one names its entry."""


@dataclass(frozen=True)
class Override:
    """A per-company pin for the rules (§9.9), logged in docs/EDGE_CASES.md."""

    cik: int
    slot: Slot
    reason: str
    source_url: str
    accession_no: str | None = None  # only this release; None: every release of the company
    row_label: str | None = None  # take the row with this label (as clean_label, any case)
    doc_name: str | None = None  # with `table`: the exhibit
    table: int | None = None  # the statement table's index in the exhibit
    not_applicable: str | None = None  # the slot doesn't apply to the company, and why

    def describe(self) -> str:
        if self.not_applicable is not None:
            return f"{self.slot}: not applicable ({self.not_applicable})"
        pins = [f"row {self.row_label!r}"] if self.row_label else []
        if self.table is not None:
            pins.append(f"table {self.table}" + (f" of {self.doc_name}" if self.doc_name else ""))
        return f"{self.slot}: {', '.join(pins)}"


def parse_overrides(
    text: str, source: str = "data/extraction_overrides.toml"
) -> tuple[Override, ...]:
    """The `[[override]]` entries of the overrides file, checked; an empty file means none."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise OverrideError(f"{source}: {exc}") from None
    entries = document.get("override", [])
    if not isinstance(entries, list) or set(document) - {"override"}:
        raise OverrideError(f"{source}: expected only [[override]] entries")
    overrides: list[Override] = []
    problems: list[str] = []
    for number, entry in enumerate(entries, start=1):
        try:
            overrides.append(_override(entry))
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"override {number}: {exc}")
    if problems:
        raise OverrideError(f"{source}:\n" + "\n".join(f"  - {line}" for line in problems))
    return tuple(overrides)


def _override(entry: object) -> Override:
    if not isinstance(entry, dict):
        raise TypeError("expected a table")
    allowed = {f for f in Override.__dataclass_fields__}
    unknown = set(entry) - allowed
    if unknown:
        raise ValueError(f"unknown keys {sorted(unknown)}")
    cik, slot = entry["cik"], entry["slot"]
    if not isinstance(cik, int) or isinstance(cik, bool) or cik <= 0:
        raise ValueError(f"cik must be a CIK (a positive whole number), got {cik!r}")
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of {', '.join(SLOTS)}, got {slot!r}")
    reason, source_url = entry["reason"], entry["source_url"]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must say why the rules need help")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        raise ValueError("source_url must be the https link to the SEC page that shows the case")
    override = Override(
        cik=cik,
        slot=slot,
        reason=reason.strip(),
        source_url=source_url,
        accession_no=_optional(entry, "accession_no", str),
        row_label=_optional(entry, "row_label", str),
        doc_name=_optional(entry, "doc_name", str),
        table=_optional(entry, "table", int),
        not_applicable=_optional(entry, "not_applicable", str),
    )
    pins = [override.row_label, override.table, override.not_applicable]
    if all(pin is None for pin in pins):
        raise ValueError("needs row_label, table or not_applicable")
    if override.not_applicable is not None and (override.row_label or override.table is not None):
        raise ValueError("not_applicable can't be combined with a row or table pin")
    return override


def _optional[T](entry: dict[str, object], key: str, kind: type[T]) -> T | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, kind) or isinstance(value, bool):
        raise ValueError(f"{key} must be a {kind.__name__}, got {value!r}")
    return value


# --- The extractor ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Located:
    """A row of the statement and what it means."""

    row: Row
    label: str  # the label matched against patterns: clean, lower case, totals named
    section: str  # the label-only rows above it, normalised ("earnings per share > diluted")
    table: Table
    document: ReleaseDocument
    heading: str = ""  # the nearest label-only row above it, as printed


@dataclass(frozen=True)
class _Statement:
    document: ReleaseDocument
    table: Table
    periods: Periods
    scale: Scale
    rows: tuple[_Located, ...]  # the table's rows and its continuation's
    titled: bool  # its title names it the statement of operations


@dataclass
class RuleExtractor:
    """§9.9's rules. `overrides` are the pins of data/extraction_overrides.toml."""

    overrides: Sequence[Override] = ()
    name: str = field(default="rules", init=False)
    version: str = field(default=RULES_VERSION, init=False)

    def extract(self, release: Release) -> ExtractionResult:
        pins = [
            override
            for override in self.overrides
            if override.cik == release.cik
            and override.accession_no in (None, release.accession_no)
            and override.not_applicable is None
        ]
        statement, notes = _statement(release, pins)
        if statement is None:
            return ExtractionResult(
                extractor=self.name,
                extractor_version=self.version,
                slots=tuple(
                    _absent(slot, release, "no income statement table with quarter columns")
                    for slot in SLOTS
                ),
                statement_found=False,
                income_lines_found=_has_income_lines(release),
                notes=tuple(notes),
            )
        current, prior = statement.periods.current, statement.periods.prior
        if current is None:
            raise ValueError("a statement table has a current quarter column")
        applied = [override.describe() for override in pins]
        results = []
        for slot in SLOTS:
            if slot in release.not_applicable:
                results.append(_absent(slot, release, release.not_applicable[slot]))
                continue
            pin = next((o for o in pins if o.slot == slot and o.row_label), None)
            found = _find(slot, statement.rows, pin.row_label if pin else None)
            results.append(_slot(slot, found, statement, current.index, prior))
        period_end = current.end or _quarter_end_in_text(
            release, statement.document, current.quarter
        )
        prior_end = prior.end if prior is not None else None
        shares = _find_shares(statement.rows)
        to_common = _find_rows(statement.rows, NET_INCOME_TO_COMMON)
        if statement.periods.problem:
            notes.append(statement.periods.problem)
        return ExtractionResult(
            extractor=self.name,
            extractor_version=self.version,
            slots=tuple(results),
            statement_found=True,
            income_lines_found=True,
            doc_name=statement.document.doc_name,
            table_index=statement.table.index,
            period_end=period_end,
            prior_period_end=prior_end,
            period_label=current.heading or None,
            diluted_shares=_figure(shares, statement, current.index, SHARES),
            net_income_to_common=_figure(to_common, statement, current.index, USD),
            notes=tuple(notes),
            overrides=tuple(applied),
        )


def _statement(release: Release, pins: Sequence[Override]) -> tuple[_Statement | None, list[str]]:
    """The statement table of the release (step 1 above)."""
    notes: list[str] = []
    table_pin = next((override for override in pins if override.table is not None), None)
    needs_revenue = "revenue" not in release.not_applicable
    candidates: list[_Statement] = []
    for document in release.documents:
        if not document.tables:
            notes.append(f"{document.doc_name}: no tables")
            continue
        for position, table in enumerate(document.tables):
            if table_pin is not None and (
                table.index != table_pin.table
                or (table_pin.doc_name and table_pin.doc_name != document.doc_name)
            ):
                continue
            context = _title_context(table)
            title = " ".join([*context, *title_lines(table)])
            if table_pin is None and _NOT_STATEMENT.search(title):
                continue
            periods = table_periods(table, context)
            if periods.current is None:
                continue
            rows = _located(table, document)
            labels = [located.label for located in rows]
            has_revenue = any(_matches(label, REVENUE) for label in labels)
            has_bottom = any(_matches(label, NET_INCOME) for label in labels) or any(
                _is_eps(located) for located in rows
            )
            titled = bool(_STATEMENT_TITLE.search(title))
            qualifies = (has_bottom and (has_revenue or not needs_revenue)) or (
                # A statement split over two tables, or one without a revenue line.
                titled
                and (has_revenue or has_bottom)
            )
            if table_pin is None and not qualifies:
                continue
            rows += _continuation(document.tables[position + 1 :], table, document)
            scale = find_scale([*context, *_heading_lines(table)])
            candidates.append(_Statement(document, table, periods, scale, tuple(rows), titled))
        if candidates:
            break  # the first exhibit with a statement is the press release (§9.9)
    if not candidates:
        return None, notes
    statements = [candidate for candidate in candidates if candidate.titled]
    return (statements or candidates)[0], notes


def _has_income_lines(release: Release) -> bool:
    """Whether any table of the exhibits has a revenue, net income or diluted EPS line."""
    for document in release.documents:
        for table in document.tables:
            for located in _located(table, document):
                label = located.label
                if _matches(label, REVENUE) or _matches(label, NET_INCOME) or _is_eps(located):
                    return True
    return False


def _continuation(
    following: Sequence[Table], table: Table, document: ReleaseDocument
) -> list[_Located]:
    """The rows of the tables that continue `table`: same width, and no title of their own
    (or one saying "continued")."""
    rows: list[_Located] = []
    for nxt in following[:2]:
        title = " ".join([*_title_context(nxt), *title_lines(nxt)])
        if nxt.width != table.width or (title and not _CONTINUED.search(title)):
            break
        rows += _located(nxt, document)
    return rows


def _title_context(table: Table) -> list[str]:
    """The table's title lines: the last three lines above it that are short enough to be
    titles; a safe-harbor paragraph that mentions non-GAAP measures is not one."""
    return [line for line in table.context[-3:] if len(line) <= TITLE_LINE]


def _located(table: Table, document: ReleaseDocument) -> list[_Located]:
    """Each row with the label patterns are matched against, and its section heading. Label-only
    rows one under the other nest ("Earnings per share > Assuming dilution")."""
    located: list[_Located] = []
    sections: list[str] = []
    heading = ""
    nesting = False
    for row in table.rows:
        label = _normal(row.clean_label)
        if not row.has_values:
            if label and not any(row.cells):
                sections = [*sections, label] if nesting else [label]
                heading = row.label
                nesting = True
            continue
        nesting = False
        section = " > ".join(sections)
        if label in ("", "total") and sections:
            # An unlabeled row (Shopify) or one labelled "Total" (Deere) closes its section.
            label = f"total {sections[-1]}"
        elif _PER_SHARE_ONLY.match(label) and located:
            label = f"{located[-1].label} {label}"  # "Net income" then "per diluted share"
        located.append(_Located(row, label, section, table, document, heading))
    return located


def _find(slot: Slot, rows: Sequence[_Located], pinned: str | None) -> _Located | None:
    if pinned is not None:
        wanted = _normal(pinned)
        return next((located for located in rows if located.label == wanted), None)
    if slot == "revenue":
        return _find_rows(rows, REVENUE)
    if slot == "operating_income":
        return _find_rows(rows, OPERATING_INCOME)
    if slot == "net_income":
        return _find_net_income(rows)
    return _find_eps(rows)


def _find_rows(rows: Sequence[_Located], patterns: Sequence[str]) -> _Located | None:
    """The row matching the best pattern; the first such row when several do."""
    best: tuple[int, int] | None = None
    found: _Located | None = None
    for position, located in enumerate(rows):
        if _excluded(located.label) or _PER_SHARE.search(located.label):
            continue
        rank = _rank(located.label, patterns)
        if rank is not None and (best is None or (rank, position) < best):
            best, found = (rank, position), located
    return found


def _find_net_income(rows: Sequence[_Located]) -> _Located | None:
    candidates = [
        located
        for located in rows
        if not _excluded(located.label)
        and not _PER_SHARE.search(located.label)
        and not _CONTINUING.search(located.label)
        and _rank(located.label, NET_INCOME) is not None
    ]
    if not candidates:
        return None
    minority = any(_NONCONTROLLING.search(located.label) for located in rows)

    def rank(located: _Located) -> int:
        base = _rank(located.label, NET_INCOME) or 0
        if minority and _TO_COMMON.search(located.label):
            return 2  # after "attributable to <company>", before the total with the minority
        if minority and base >= 3:
            return base + 10
        return base

    return min(candidates, key=lambda located: (rank(located), rows.index(located)))


def _find_eps(rows: Sequence[_Located]) -> _Located | None:
    ranked = [
        (rank, position, located)
        for position, located in enumerate(rows)
        if (rank := _eps_rank(located)) is not None
    ]
    return min(ranked, key=lambda item: item[:2])[2] if ranked else None


def _is_eps(located: _Located) -> bool:
    return _eps_rank(located) is not None


def _eps_rank(located: _Located) -> int | None:
    """How good a diluted EPS line this row is (lower is better); None: not one. Total EPS
    before EPS from continuing operations."""
    label, section = located.label, located.section
    if _EXCLUDED.search(label) or _SECTION_EXCLUDED.search(section) or _SHARE_COUNT.search(label):
        return None
    continuing = 1 if _CONTINUING.search(label) else 0
    if _matches(label, EPS):
        return continuing
    if not _PER_SHARE.search(section) or _SHARE_COUNT.search(section):
        return None
    if _matches(label, DILUTED_IN_SECTION) and "dilut" not in section.rsplit(" > ", 1)[-1]:
        return 2 + continuing  # "Earnings per share:" then "Diluted"
    if "dilut" not in section:
        return None
    if _matches(label, TOTAL_IN_SECTION):
        return 2
    share_class = _CLASS.match(label)
    if share_class is not None:
        return 3 + ord(share_class.group("letter")) - ord("a")  # Class A first
    if continuing:
        return 30
    return None


def _find_shares(rows: Sequence[_Located]) -> _Located | None:
    """The diluted weighted-average share count, for the EPS check (§9.9)."""
    for located in rows:
        label = located.label
        if _EXCLUDED.search(label):
            continue
        counts = bool(_SHARE_COUNT.search(label)) or _matches(label, SHARES_DILUTED)
        if counts and ("dilut" in label) and not _eps_label(label):
            return located
        in_shares = bool(_SHARE_COUNT.search(located.section))
        if in_shares and (_matches(label, DILUTED_IN_SECTION) or "dilut" in label):
            return located
    return None


def _eps_label(label: str) -> bool:
    """An EPS line rather than a share count: its label names the amount per share first."""
    return bool(
        re.match(r"^(?:diluted |basic and diluted )?(?:net )?\(?(?:earnings|income|loss)", label)
    )


def _slot(
    slot: Slot,
    found: _Located | None,
    statement: _Statement,
    current: int,
    prior_column: Column | None,
) -> SlotResult:
    unit = SLOT_UNITS[slot]
    if found is None:
        return SlotResult(slot, "not_found", unit, METHOD, note="no row matched the slot's labels")
    per_share = unit != USD
    # A row's own scale wins over the table's: "Revenue ($ in thousands)".
    row_scale = find_scale([found.row.label, found.heading])
    scale = Decimal(1) if per_share else (row_scale.money or statement.scale.money)
    scale_text = statement.scale.text if row_scale.money is None else row_scale.text
    flags: list[str] = []
    if scale is None:
        return SlotResult(
            slot,
            "failed",
            unit,
            METHOD,
            flags=("scale_unknown",),
            note=f"row {found.row.label!r}: the table states no scale",
        )
    cell = found.row.cells[current]
    value = parse_number(cell)
    if value is None:
        return SlotResult(
            slot,
            "failed",
            unit,
            METHOD,
            note=f"row {found.row.label!r}: no number in the current column ({cell!r})",
        )
    current_figure = _make_figure(found, cell, value, scale)
    prior_figure = None
    if prior_column is not None:
        prior_cell = found.row.cells[prior_column.index]
        prior_value = parse_number(prior_cell)
        if prior_value is not None:
            prior_figure = _make_figure(found, prior_cell, prior_value, scale)
    return SlotResult(
        slot,
        "found",
        unit,
        METHOD,
        current=current_figure,
        prior=prior_figure,
        scale_text=scale_text,
        flags=tuple(flags),
    )


def _figure(
    found: _Located | None, statement: _Statement, current: int, unit: str
) -> Figure | None:
    if found is None:
        return None
    # A row's own scale wins: Costco's "Shares used in calculation (000's)" under a table "in
    # millions".
    row_scale = find_scale([found.row.label, found.heading]).money
    table_scale = statement.scale.shares if unit == SHARES else statement.scale.money
    scale = row_scale or table_scale
    value = parse_number(found.row.cells[current])
    if scale is None or value is None:
        return None
    return _make_figure(found, found.row.cells[current], value, scale)


def _make_figure(found: _Located, cell: str, value: Decimal, scale: Decimal) -> Figure:
    return Figure(
        value=value * scale,
        cell=cell,
        scale=scale,
        doc_name=found.document.doc_name,
        row_label=found.row.label,
        quote=found.row.line,
    )


def _absent(slot: Slot, release: Release, why: str) -> SlotResult:
    if slot in release.not_applicable:
        reason = release.not_applicable[slot]
        return SlotResult(slot, "not_applicable", SLOT_UNITS[slot], METHOD, note=reason)
    return SlotResult(slot, "not_found", SLOT_UNITS[slot], METHOD, note=why)


def parse_number(cell: str) -> Decimal | None:
    """The number a printed cell holds: `$` and spaces dropped, parentheses negative, a dash
    nil (zero). None for an empty cell or text."""
    text = _FOOTNOTE_SUFFIX.sub("", cell.strip())
    text = text.replace("$", "").replace(",", "").replace(" ", "").replace("−", "-")
    if not text:
        return None
    if _NIL.match(text):
        return Decimal(0)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative else number


def _quarter_end_in_text(
    release: Release, statement: ReleaseDocument, quarter: int | None
) -> date | None:
    """When the headings give no day ("Q3 FY26", "Fourth Quarter 2025"), the quarter's end as
    the exhibits state it: "for the three months ended September 30, 2026". A date counts when
    it ended before the filing and, if the column names its quarter, falls in that fiscal
    quarter (within RELEASE_LAG); if it doesn't, within UNNAMED_LAG, too soon after the filing
    for the quarter before. The latest such date; None when the text doesn't settle it."""
    if release.filed_at is None:
        return None
    filed = release.filed_at
    found = []
    others = [document for document in release.documents if document is not statement]
    for document in [statement, *others]:
        for match in _QUARTER_ENDED.finditer(document.text):
            end = heading_date(document.text[match.end() : match.end() + 30])
            if end is None or end > filed:
                continue
            if quarter is None:
                if filed - end <= UNNAMED_LAG:
                    found.append(end)
                continue
            label = fiscal_quarter(end, release.fiscal_year_end)
            if filed - end <= RELEASE_LAG and label and label.fiscal_period == f"Q{quarter}":
                found.append(end)
    return max(found) if found else None


def _heading_lines(table: Table) -> list[str]:
    return [
        " ".join(part for part in (row.label, *row.cells) if part) for row in heading_rows(table)
    ]


def _excluded(label: str) -> bool:
    """Never a slot's line ("Adjusted net income", "Comprehensive income"), except the net
    income line some companies print as "Net loss and comprehensive loss" (CoreWeave)."""
    return bool(_EXCLUDED.search(_NET_AND_COMPREHENSIVE.sub("net income", label)))


_NET_AND_COMPREHENSIVE = re.compile(
    r"^net (?:\(loss\) )?(?:income|earnings|loss)(?: \(loss\))? and comprehensive "
    r"(?:\(loss\) )?(?:income|loss)(?: \(loss\))?$"
)


def _normal(label: str) -> str:
    """A label as patterns see it: lower case, dashes and spaces even, "(unaudited)" and
    "(EPS)" dropped, and a footnote digit glued to the last word dropped ("Per Share1",
    "Corporate3": Coca-Cola and Nike print them without superscript)."""
    text = label.lower().replace("&", " and ").replace("’", "'")
    text = re.sub(r"[\u00b9\u00b2\u00b3\u2070-\u209f]+", "", text)  # superscript markers
    # Dashes between words even out ("Per Share — diluted"); hyphens inside words stay
    # ("non-controlling", "weighted-average").
    text = re.sub(r"\s*[—–‒―]\s*", " - ", text)
    text = re.sub(r"\s+-\s*|\s*-\s+", " - ", text)
    text = re.sub(r"\((?:unaudited|eps|u\.s\. dollars|in dollars|dollars|\$)\)", "", text)
    text = re.sub(r"\s*/\s*\(", " (", text)  # "Net income/(loss)" (ExxonMobil)
    text = re.sub(r"\s+", " ", text).strip().rstrip(":;,. ")
    return re.sub(r"(?<=[a-z]{2})\d{1,2}$", "", text)


def _rank(label: str, patterns: Sequence[str]) -> int | None:
    for rank, pattern in enumerate(patterns):
        if re.fullmatch(pattern, label):
            return rank
    return None


def _matches(label: str, patterns: Sequence[str]) -> bool:
    return _rank(label, patterns) is not None
