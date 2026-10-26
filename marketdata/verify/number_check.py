"""Checks that each value appears in the stored document and matches its scale (E7a-S4,
PLAN.md §9.9 checks 1 and 2).

Pure, and independent of the extractor (D-015): it never imports extract/, reads numbers with
its own rules, and checks values against `documents.text`, whoever produced them. A claim is
one value an extractor says it read: the cell as printed, the scale it applied, and the row it
quotes.

1. In the source: the quote is a line of the document text, and the cell is one of the
   quote's cells (whitespace normalised on both sides).
2. Scale: the value equals the cell's number times the scale, exactly; and the scale is the
   one the document states: the scale words the extractor cites are in the text, and read
   here, on their own, they give that multiplier. Per-share values are never scaled.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

IN_SOURCE = "in_source"
SCALE = "scale"

_SPACE = re.compile(r"\s+")
_CELL_SEPARATOR = re.compile(r"(?<!\\)\|")  # markdown cells; "\|" is a pipe in a cell
_SCALE_WORDS = re.compile(r"(?P<word>thousand|million|billion)s?\b|\b0{3}'?s\b|\$\s*0{3}", re.I)
_MULTIPLIERS = {
    "thousand": Decimal(1000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
}
_DASH = re.compile(r"^[—–‒―−-]+$")
_MARKER = re.compile(r"(?<=[\d)])(?:\s+\(\s*(?:[a-z]{1,2}|\d)\s*\)|\s*\*+|\s*[†‡§]+)$", re.I)


@dataclass(frozen=True)
class Claim:
    """A value an extractor read, and where."""

    name: str  # what it is: "revenue", "revenue (prior year)", "diluted shares"
    value: Decimal
    cell: str  # as printed
    scale: Decimal
    quote: str  # the row as printed
    doc_name: str


@dataclass(frozen=True)
class Problem:
    check: str  # IN_SOURCE or SCALE
    message: str


def check_claim(claim: Claim, text: str) -> list[Problem]:
    """Both checks for one claim against its document's stored text; [] when it passes."""
    problems = []
    if not in_source(claim, text):
        problems.append(
            Problem(
                IN_SOURCE,
                f"{claim.name}: {claim.cell!r} in {claim.quote!r} is not in "
                f"{claim.doc_name}'s text",
            )
        )
    number = read_number(claim.cell)
    if number is None:
        problems.append(Problem(SCALE, f"{claim.name}: {claim.cell!r} is not a number"))
    elif number * claim.scale != claim.value:
        problems.append(
            Problem(
                SCALE,
                f"{claim.name}: {claim.cell!r} × {claim.scale} is "
                f"{number * claim.scale}, not {claim.value}",
            )
        )
    return problems


def scale_problem(
    scale: Decimal, scale_text: str | None, text: str, *, per_share: bool
) -> Problem | None:
    """Whether `scale` is the scale the document states for a value (check 2, second half)."""
    if per_share:
        return None if scale == 1 else Problem(SCALE, f"a per-share value scaled by {scale}")
    if not scale_text:
        return Problem(SCALE, "no scale words are cited")
    if _normal(scale_text) not in _normal(text):
        return Problem(SCALE, f"the scale words {scale_text!r} are not in the text")
    stated = read_scale(scale_text)
    if stated != scale:
        return Problem(SCALE, f"{scale_text!r} states a scale of {stated}, not {scale}")
    return None


def read_scale(words: str) -> Decimal | None:
    """The multiplier scale words state: thousands, millions, billions, or "000s"."""
    match = _SCALE_WORDS.search(words)
    if match is None:
        return None
    word = (match.group("word") or "thousand").lower()
    return _MULTIPLIERS[word]


def in_source(claim: Claim, text: str) -> bool:
    quote = _normal(claim.quote)
    if not quote or _normal(claim.cell) == "":
        return False
    lines = {_normal(line) for line in text.splitlines()}
    if quote not in lines:
        return False
    cells = [cell.strip() for cell in _CELL_SEPARATOR.split(quote.strip().strip("|"))]
    return _normal(claim.cell) in cells[1:]


def read_number(cell: str) -> Decimal | None:
    """A printed number: `$`, spaces and thousands separators dropped, parentheses negative,
    a dash nil (zero), a footnote marker after it ignored."""
    text = _MARKER.sub("", cell.strip())
    text = text.replace("$", "").replace(",", "").replace(" ", "").replace("−", "-")
    if not text:
        return None
    if _DASH.match(text):
        return Decimal(0)
    negative = len(text) > 2 and text[0] == "(" and text[-1] == ")"
    if negative:
        text = text[1:-1]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return -number if negative else number


def _normal(text: str) -> str:
    return _SPACE.sub(" ", text).strip()
