"""Form families (D-139): the SEC form codes a requested form matches, for the `form` filter of a
company's filings (REST `/v1/companies/{ticker}/filings`, MCP `recent_filings`, the CLI).

Pure. A form matches its family: itself, its amendment (`/A`) and its other SEC codes. The SEC
gave Schedules 13D and 13G new codes when they became structured XML in 2024 (`SC 13G` became
`SCHEDULE 13G`, D-061), so either code, or the short `13G`, matches both. A 10-K takes in the
transition report filed in its place (10-KT), a 10-Q its 10-QT and an 8-K the 8-Ks of a
successor issuer (8-K12B, 8-K12G3, 8-K15D5), as the snapshot and the facts routing count the
transition reports and the labels the successor 8-Ks; asked for by its own code, such a form
matches itself and its amendment. An amendment asked for (`8-K/A`, `13G/A`) matches the
family's amendments only. Any other form (S-1, 424B2, 4) matches itself and its amendment; the
SEC's other amendment codes (`S-8 POS`, `POS AM`, `DEFR14A`) are forms of their own.

Text is read the way clients type it: any case, extra spaces, typographic dashes, a leading
"Form". One form at a time: a list (`8-K, 10-Q`) is refused, not matched as nothing.
"""

import re

AMENDMENT = "/A"

# The codes of one form, the current one first: Schedules 13D and 13G before and after their
# structured XML (D-061).
_SAME_FORM: tuple[tuple[str, ...], ...] = (
    ("SCHEDULE 13D", "SC 13D"),
    ("SCHEDULE 13G", "SC 13G"),
)
# Forms filed in a form's place, which asking for the form takes in.
TAKES_IN: dict[str, tuple[str, ...]] = {
    "10-K": ("10-KT",),
    "10-Q": ("10-QT",),
    "8-K": ("8-K12B", "8-K12G3", "8-K15D5"),
}
# Every 13D and 13G code, amendments included: forms about a company that its filer's listing
# shows too (D-061).
STAKE_CODES: frozenset[str] = frozenset(
    code + amendment for codes in _SAME_FORM for code in codes for amendment in ("", AMENDMENT)
)

_DASHES = re.compile("[‐-―−]")  # hyphens, dashes and minus as people type them
_SPACES = re.compile(r"\s+")
_AROUND_SLASH = re.compile(r" ?/ ?")
_FORM_WORD = re.compile(r"FORM (?=\S)")
_STAKE = re.compile(r"(?:(SC|SCHEDULE) ?)?13([DG])")
_CODE = re.compile(r"[A-Z0-9][A-Z0-9 /-]*")  # SEC form codes: 8-K, SC TO-T/A, S-8 POS, 40-17G
EXAMPLES = "e.g. 8-K, 10-K/A, 4 or SCHEDULE 13G"


def normalized(text: str) -> str:
    """A form code as the SEC writes it: upper case, single spaces, ASCII hyphens, no space
    around `/`, without a leading "Form"."""
    code = _SPACES.sub(" ", _DASHES.sub("-", text).upper()).strip()
    return _FORM_WORD.sub("", _AROUND_SLASH.sub("/", code), count=1)


def form_family(asked: str) -> tuple[str, ...]:
    """The form codes a requested form matches, each followed by its amendment: the form as
    asked first (`13G` as the current `SCHEDULE 13G`), then its other codes and the forms it
    takes in. An amendment matches the family's amendments only. Raises ValueError for text
    that names no form, or more than one."""
    code = normalized(asked)
    if not _CODE.fullmatch(code):
        raise ValueError(
            f"form {asked!r} isn't one SEC form code ({EXAMPLES}); ask for one form at a time"
        )
    amendments_only = code.endswith(AMENDMENT)  # never all of it: a code starts with A-Z or 0-9
    base = code.removesuffix(AMENDMENT)
    stake = _STAKE.fullmatch(base)
    if stake:
        prefix, kind = stake.groups()
        base = f"{'SC' if prefix == 'SC' else 'SCHEDULE'} 13{kind}"
    same = next((codes for codes in _SAME_FORM if base in codes), (base,))
    bases = (base, *(other for other in same if other != base), *TAKES_IN.get(base, ()))
    if amendments_only:
        return tuple(each + AMENDMENT for each in bases)
    return tuple(code for each in bases for code in (each, each + AMENDMENT))
