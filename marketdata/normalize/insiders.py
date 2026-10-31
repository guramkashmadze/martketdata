"""Insider trades (PLAN.md §9.7, E8): what a parsed Form 4 stores, its transaction codes, and
which lines a 4/A or a filing made twice supersedes.

Pure: takes parsed filings and stored lines and returns records and decisions.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from marketdata.normalize.changes import ValueChange
from marketdata.normalize.form4 import Form4

# The SEC's Form 4 transaction codes (General Instruction 8), in plain words. Other codes are
# stored and served as filed.
CODE_LABELS = {
    "P": "Purchase (open market or private)",
    "S": "Sale (open market or private)",
    "A": "Grant or award",
    "D": "Disposition to the issuer",
    "F": "Shares withheld or delivered to pay an exercise price or tax",
    "M": "Exercise or conversion of a derivative (exempt)",
    "C": "Conversion of a derivative",
    "X": "Exercise of an in-the-money or at-the-money derivative",
    "O": "Exercise of an out-of-the-money derivative",
    "E": "Expiration of a short derivative position",
    "H": "Expiration or cancellation of a long derivative position with value received",
    "G": "Gift",
    "I": "Discretionary transaction",
    "J": "Other acquisition or disposition (see footnotes)",
    "K": "Equity swap or similar instrument",
    "L": "Small acquisition",
    "U": "Disposition in a change-of-control tender",
    "W": "Acquisition or disposition by will or inheritance",
    "Z": "Deposit into or withdrawal from a voting trust",
}


@dataclass(frozen=True)
class FilingRecord:
    """A parsed Form 4 and where it came from: the issuer's listing and the stored XML."""

    accession_no: str
    filed_at: date
    doc_name: str  # the XML in the filing folder
    raw_key: str
    form4: Form4

    @property
    def owner_ciks(self) -> tuple[int, ...]:
        """The reporting owners' CIKs, sorted: a 4/A amends a filing by the same owners."""
        return tuple(sorted({owner.cik for owner in self.form4.owners}))


# --- Amendments and duplicates (E8-S3) ----------------------------------------------------
#
# A 4/A names the date of the filing it amends (`dateOfOriginalSubmission`). The filings it
# amends are those its owners filed about the issuer that day. Of the 56 4/As the universe's
# insiders filed in two years (EDGE_CASES.md, E8-S3), 26 restate the filing's lines with the
# same tables, securities and codes (20 of them correct values or a date), 4 have no lines (a
# power of attorney attached), 7 restate only the lines they correct, some add omitted trades,
# and some restate a filing with new structure. So a 4/A:
#
# 1. without lines supersedes nothing;
# 2. restates a filing in full when it repeats one of its lines unchanged, or has the same
#    lines by table, security and code (and no other filing of that day has): every line of
#    that filing is superseded;
# 3. otherwise supersedes only the lines it corrects: of the filing's lines with its table,
#    security, code and trade date, the only one, else the only one with its ownership, else
#    the only one that differs in at most one of shares, price, holdings after, acquired or
#    disposed, and ownership; failing that, the only line with its table, security and code
#    when it has the only such line (a corrected trade date). Its other lines are trades the
#    original left out.
#
# A later 4/A of the same filing amends the earlier one too. A filing made twice the same day
# (the same owners, form and lines) is served once, from the copy with the lowest accession
# number. An amendment overrides a duplicate, and a later 4/A an earlier one.

type SupersedeReason = Literal["amendment", "duplicate"]
type ChangeReason = Literal["correction", "amendment"]  # a duplicate's removal is a correction
type LineKey = tuple[str, int]  # accession number, line number


class TradeLine(Protocol):
    """A trade as the amendment rules read it: a parsed or a stored line."""

    @property
    def line_no(self) -> int: ...
    @property
    def is_derivative(self) -> bool: ...
    @property
    def security_title(self) -> str | None: ...
    @property
    def trade_date(self) -> date: ...
    @property
    def code(self) -> str: ...
    @property
    def acquired_disposed(self) -> str | None: ...
    @property
    def shares(self) -> Decimal | None: ...
    @property
    def price(self) -> Decimal | None: ...
    @property
    def owned_after(self) -> Decimal | None: ...
    @property
    def direct_indirect(self) -> str | None: ...
    @property
    def nature_of_ownership(self) -> str | None: ...


@dataclass(frozen=True)
class GroupFiling:
    """One filing of an insider group: the Form 4s and 4/As the same owners filed about one
    issuer."""

    accession_no: str
    form: str
    filed_at: date
    original_filed_at: date | None
    lines: Sequence[TradeLine]


@dataclass(frozen=True)
class Supersession:
    by: str  # the 4/A, or the copy of a duplicate filing that is served
    by_line: int | None  # the line that restates or corrects it there, when one does
    reason: SupersedeReason


@dataclass(frozen=True)
class GroupDecision:
    superseded: Mapping[LineKey, Supersession]
    unmatched: tuple[str, ...]  # 4/As that name no filing of the group


def group_supersessions(filings: Sequence[GroupFiling]) -> GroupDecision:
    """Which lines of an insider group are superseded, and by what."""
    ordered = sorted(filings, key=lambda filing: (filing.filed_at, filing.accession_no))
    superseded: dict[LineKey, Supersession] = {}
    copies: dict[tuple[str, date], list[GroupFiling]] = {}
    for filing in ordered:
        copies.setdefault((filing.form, filing.filed_at), []).append(filing)
    for group in copies.values():
        superseded.update(_duplicates(group))
    unmatched = []
    for amendment in ordered:
        if amendment.form != "4/A" or not amendment.lines:
            continue
        originals = [
            filing
            for filing in ordered
            if filing.accession_no != amendment.accession_no
            and (
                filing.filed_at == amendment.original_filed_at
                or (  # an earlier amendment of the same filing
                    filing.form == "4/A"
                    and filing.original_filed_at == amendment.original_filed_at
                    and (filing.filed_at, filing.accession_no)
                    < (amendment.filed_at, amendment.accession_no)
                )
            )
        ]
        if not originals:
            unmatched.append(amendment.accession_no)
            continue
        # The filing as served now: lines an earlier 4/A or a first copy took the place of
        # are compared through the lines that took it.
        current = [
            GroupFiling(
                filing.accession_no,
                filing.form,
                filing.filed_at,
                filing.original_filed_at,
                [
                    line
                    for line in filing.lines
                    if (filing.accession_no, line.line_no) not in superseded
                ],
            )
            for filing in originals
        ]
        found = amendment_supersessions(amendment, [filing for filing in current if filing.lines])
        for key, supersession in found.items():
            superseded[key] = supersession
            for earlier, previous in list(superseded.items()):
                if (previous.by, previous.by_line) == key:  # it served this line's trade
                    superseded[earlier] = supersession
    return GroupDecision(superseded, tuple(unmatched))


def amendment_supersessions(
    amendment: GroupFiling, originals: Sequence[GroupFiling]
) -> dict[LineKey, Supersession]:
    """The lines of `originals` (the filings as served now) a 4/A supersedes (rules 1 to 3
    above)."""
    if not amendment.lines:
        return {}
    free = list(amendment.lines)
    found: dict[LineKey, Supersession] = {}
    repeated = {
        original.accession_no
        for original in originals
        if any(same_trade(new, old) for new in amendment.lines for old in original.lines)
    }
    alike = [
        original.accession_no
        for original in originals
        if Counter(map(_group, amendment.lines)) == Counter(map(_group, original.lines))
    ]
    # Several filings of the day with the 4/A's lines: which it restates isn't known, so each
    # loses only the lines the 4/A corrects.
    restated = repeated | (set(alike) if len(alike) == 1 else set())
    for original in originals:
        if original.accession_no not in restated:
            continue
        pairs = _pairs(free, [(original.accession_no, line) for line in original.lines])
        for line in original.lines:
            by = pairs.get((original.accession_no, line.line_no))
            found[(original.accession_no, line.line_no)] = Supersession(
                amendment.accession_no, by.line_no if by is not None else None, "amendment"
            )
    pool = [
        (original.accession_no, line)
        for original in originals
        if original.accession_no not in restated
        for line in original.lines
    ]
    for key, by in _pairs(free, pool).items():
        found[key] = Supersession(amendment.accession_no, by.line_no, "amendment")
    return found


def same_trade(a: TradeLine, b: TradeLine) -> bool:
    """The same line as filed: table, security, date, code and values."""
    return _identity(a) == _identity(b) and _values(a) == _values(b)


def _duplicates(copies: Sequence[GroupFiling]) -> dict[LineKey, Supersession]:
    found: dict[LineKey, Supersession] = {}
    served: list[GroupFiling] = []
    for filing in copies:
        first = next((kept for kept in served if _repeats(filing, kept)), None)
        if first is None:
            served.append(filing)
            continue
        for line in filing.lines:
            found[(filing.accession_no, line.line_no)] = Supersession(
                first.accession_no, line.line_no, "duplicate"
            )
    return found


def _repeats(filing: GroupFiling, kept: GroupFiling) -> bool:
    return (
        bool(filing.lines)
        and len(filing.lines) == len(kept.lines)
        and all(same_trade(a, b) for a, b in zip(filing.lines, kept.lines, strict=True))
    )


def _pairs(
    free: list[TradeLine], pool: Sequence[tuple[str, TradeLine]]
) -> dict[LineKey, TradeLine]:
    """Original line -> the 4/A line that restates or corrects it, over the lines of every
    filing in `pool`. Paired 4/A lines are taken out of `free`."""
    pairs: dict[LineKey, TradeLine] = {}
    open_lines = list(pool)

    def pair(old: tuple[str, TradeLine], new: TradeLine) -> None:
        pairs[(old[0], old[1].line_no)] = new
        open_lines.remove(old)
        free.remove(new)

    for new in list(free):
        match = next((old for old in open_lines if same_trade(new, old[1])), None)
        if match is not None:
            pair(match, new)
    for new in list(free):
        candidates = [old for old in open_lines if _identity(old[1]) == _identity(new)]
        chosen = _corrected(new, [line for _, line in candidates])
        if chosen is not None:
            pair(next(old for old in candidates if old[1] is chosen), new)
    for new in list(free):
        group = [old for old in open_lines if _group(old[1]) == _group(new)]
        if len(group) == 1 and sum(1 for other in free if _group(other) == _group(new)) == 1:
            pair(group[0], new)
    return pairs


def _corrected(new: TradeLine, candidates: Sequence[TradeLine]) -> TradeLine | None:
    if len(candidates) == 1:
        return candidates[0]
    owned = [old for old in candidates if _ownership(old) == _ownership(new)]
    if len(owned) == 1:
        return owned[0]
    close = [old for old in owned or candidates if _differences(new, old) <= 1]
    return close[0] if len(close) == 1 else None


def _title(line: TradeLine) -> str:
    return " ".join((line.security_title or "").lower().split())


def _group(line: TradeLine) -> tuple[bool, str, str]:
    return (line.is_derivative, _title(line), line.code)


def _identity(line: TradeLine) -> tuple[bool, str, str, date]:
    return (*_group(line), line.trade_date)


def _ownership(line: TradeLine) -> tuple[str | None, str]:
    return (line.direct_indirect, " ".join((line.nature_of_ownership or "").lower().split()))


def _values(line: TradeLine) -> tuple[object, ...]:
    return (line.shares, line.price, line.owned_after, line.acquired_disposed, _ownership(line))


def _differences(a: TradeLine, b: TradeLine) -> int:
    return sum(1 for x, y in zip(_values(a), _values(b), strict=True) if x != y)


# --- Changes to served lines (D-027) -----------------------------------------------------
#
# A line is served while nothing supersedes it. One value change per line whose served trade
# changes: `shares` old and new (null when no line takes its place), the rest in the detail.


def supersession_changes(
    lines: Mapping[LineKey, TradeLine],
    before: Mapping[LineKey, Supersession | None],
    after: Mapping[LineKey, Supersession],
) -> list[ValueChange]:
    """The changes to served trades when an insider group's supersessions go from `before`
    (every line stored before; None: served) to `after` (the superseded lines). Lines new in
    this run were never served, so they change nothing."""
    changes = []
    for key in sorted(before):
        was, now = before[key], after.get(key)
        if key not in lines or _replacement(was) == _replacement(now):
            continue
        # A duplicate's trade is served from its first copy, which was served already: the
        # copy's line goes, it isn't replaced.
        old_line = lines[key] if was is None else _line_of(lines, was)
        new_line = lines[key] if now is None else _line_of(lines, now)
        if old_line is not None and new_line is not None and same_trade(old_line, new_line):
            continue
        accession_no, line_no = key
        reason: ChangeReason = (
            "amendment" if now is not None and now.reason == "amendment" else "correction"
        )
        changes.append(
            ValueChange(
                {
                    "accession_no": accession_no,
                    "line_no": str(line_no),
                    "field": "shares",
                    "served_from": now.by if now is not None else accession_no,
                },
                old_line.shares if old_line is not None else None,
                new_line.shares if new_line is not None else None,
                reason,
                _detail(key, now, old_line, new_line),
            )
        )
    return changes


def reparse_changes(
    accession_no: str,
    stored: Mapping[int, TradeLine],
    parsed: Sequence[TradeLine],
    *,
    old_version: str,
    new_version: str,
) -> list[ValueChange]:
    """The changes a new parse of a stored filing makes to its served lines (a correction)."""
    changes = []
    new = {line.line_no: line for line in parsed}
    for line_no in sorted(stored):
        old_line, new_line = stored[line_no], new.get(line_no)
        if new_line is not None and same_trade(old_line, new_line):
            continue
        what = _differences_text(old_line, new_line) if new_line else "the line is gone"
        changes.append(
            ValueChange(
                {
                    "accession_no": accession_no,
                    "line_no": str(line_no),
                    "field": "shares",
                    "served_from": accession_no,
                },
                old_line.shares,
                new_line.shares if new_line is not None else None,
                "correction",
                f"parser version {old_version} to {new_version}: line {line_no} of "
                f"{accession_no}: {what}",
            )
        )
    return changes


def _replacement(supersession: Supersession | None) -> tuple[str, int | None] | None:
    return None if supersession is None else (supersession.by, supersession.by_line)


def _line_of(lines: Mapping[LineKey, TradeLine], supersession: Supersession) -> TradeLine | None:
    """The line that serves a superseded line's trade in its place, if any."""
    if supersession.by_line is None or supersession.reason == "duplicate":
        return None
    return lines.get((supersession.by, supersession.by_line))


def _detail(
    key: LineKey, now: Supersession | None, old: TradeLine | None, new: TradeLine | None
) -> str:
    accession_no, line_no = key
    if now is None:
        return f"line {line_no} of {accession_no} is served again"
    if now.reason == "duplicate":
        return (
            f"{accession_no} repeats {now.by}, filed the same day: line {line_no} is served "
            f"once, from {now.by}"
        )
    if new is None:
        return f"4/A {now.by} restates {accession_no} without its line {line_no}"
    what = _differences_text(old, new) if old is not None else "a new line"
    return f"4/A {now.by} line {now.by_line} corrects line {line_no} of {accession_no}: {what}"


def _differences_text(old: TradeLine, new: TradeLine) -> str:
    fields = (
        ("trade date", old.trade_date, new.trade_date),
        ("code", old.code, new.code),
        ("security", old.security_title, new.security_title),
        ("shares", old.shares, new.shares),
        ("price", old.price, new.price),
        ("acquired or disposed", old.acquired_disposed, new.acquired_disposed),
        ("holdings after", old.owned_after, new.owned_after),
        ("direct or indirect", old.direct_indirect, new.direct_indirect),
        ("nature of ownership", old.nature_of_ownership, new.nature_of_ownership),
    )
    changed = [f"{name} {_text(a)} to {_text(b)}" for name, a, b in fields if a != b]
    return "; ".join(changed) or "the same trade"


def _text(value: object) -> str:
    return "none" if value is None else str(value)
