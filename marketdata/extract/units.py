"""Scale from table captions and headers (E7a-S3, PLAN.md §9.9).

Pure. A press release states its scale once, above or at the top of a table: "(In millions,
except per share amounts)", "($ in thousands)", "(Dollars in millions)". Money values are
multiplied by it; per-share values never are. Share counts follow the money scale unless the
text says otherwise: "except share and per share amounts" leaves them whole, and Apple writes
"(In millions, except number of shares, which are reflected in thousands, and per-share
amounts)". No scale stated: the slot fails with `scale_unknown` (§9.9), never a guess.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

THOUSAND = Decimal(1000)
MILLION = Decimal(1_000_000)
BILLION = Decimal(1_000_000_000)

_WORDS = {"thousand": THOUSAND, "million": MILLION, "billion": BILLION}
# "in millions", "$ in millions", "dollars in thousands", "(millions)", "US $ millions",
# "amounts in thousands", "000s omitted", "$000".
_SCALE = re.compile(
    r"\b(?:in|of)\s+(?:(?:u\.?s\.?|us|\$|dollars?|usd)\s*)*(?P<word>thousand|million|billion)s?\b"
    r"|\(\s*(?:\$|us\s*\$|u\.s\.\s*\$|dollars|usd)?\s*(?P<bare>thousand|million|billion)s?\b"
    r"|(?:\bus\s*)?\$\s*(?P<dollar>thousand|million|billion)s?\b"
    r"|(?P<omitted>\b0{3}'?s?\b\s*(?:omitted)?|\$\s*0{3}s?\b|\(\s*0{3}'?s?\s*\))",
    re.IGNORECASE,
)
# Share counts scaled differently: "shares in thousands", "shares, which are reflected in
# thousands", "shares are in millions".
_SHARES_SCALE = re.compile(
    r"\bshares?\b[^.;)]{0,40}?\b(?:in|of)\s+(?P<word>thousand|million|billion)s?\b",
    re.IGNORECASE,
)
# "except share and per share data", "except par value and share data": counts are whole;
# "except per share amounts", "except earnings per share" say nothing about counts.
_EXCEPT = re.compile(r"\bexcept\b(?P<clause>[^.;)]*)", re.IGNORECASE)
_PER_SHARE = re.compile(r"\bper[- ]shares?\b", re.IGNORECASE)
_SHARE = re.compile(r"\bshares?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Scale:
    """What printed numbers are multiplied by."""

    money: Decimal | None  # None: no scale stated
    shares: Decimal | None
    text: str | None  # the words that stated it, as printed


def find_scale(lines: list[str]) -> Scale:
    """The scale stated in these lines (a table's title lines and headings), the first stated
    wins. Per-share values are never scaled."""
    for line in lines:
        match = _SCALE.search(line)
        if match is None:
            continue
        money = _value(match)
        return Scale(money, _shares(line, money), _phrase(line, match))
    return Scale(None, None, None)


def _value(match: re.Match[str]) -> Decimal:
    word = match.group("word") or match.group("bare") or match.group("dollar")
    return _WORDS[word.lower()] if word else THOUSAND


def _shares(line: str, money: Decimal) -> Decimal | None:
    explicit = _SHARES_SCALE.search(line)
    if explicit is not None:
        return _WORDS[explicit.group("word").lower()]
    excepted = _EXCEPT.search(line)
    if excepted is not None and _SHARE.search(_PER_SHARE.sub("", excepted.group("clause"))):
        return Decimal(1)
    return money


def _phrase(line: str, match: re.Match[str]) -> str:
    """The parenthesised note around the match, or the line up to the column headings that
    follow it ("Dollars in millions (unless otherwise noted) Three Months Ended …")."""
    start = line.rfind("(", 0, match.start() + 1)
    end = line.find(")", match.end())
    if start != -1 and end != -1 and line.find("(", start + 1, match.start()) == -1:
        return line[start : end + 1]
    head = _HEADINGS.split(line[match.end() :], maxsplit=1)[0]
    return (line[: match.end()] + head).strip()[:160]


_HEADINGS = re.compile(
    r"\s+(?=(?:three|six|nine|twelve|for the|quarter|year|fiscal|q[1-4]\b|[1-4]q|\d{4}\b))",
    re.IGNORECASE,
)
