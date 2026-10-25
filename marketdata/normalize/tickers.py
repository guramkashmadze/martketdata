"""Ticker spelling, and the security type a ticker's suffix shows (PLAN.md §9.2, D-051).

The SEC lists write tickers in upper case with '-' before a class or series suffix (`BRK-B`).
People and other systems write `brk.b` or `BRK/B`, and the fund list has entries such as
`elfnx` and `(NWAKX)`. `canonical_ticker` turns all of them into the SEC's form, so stored
tickers and lookups always agree.

The SEC lists carry no security type. Suffixes show some types reliably: `JPM-PC` is a
preferred, `VAL-WT` a warrant, `BRK-A` a share class. A plain ticker is taken as common stock,
which is wrong for a few (Alphabet's GOOGM is a depositary share of a preferred, JPMorgan's
AMJB an ETN); data/security_overrides.csv corrects those, each with the cover page showing it.
"""

import re
from typing import Literal

SecurityType = Literal["common", "preferred", "fund", "adr", "other"]
SECURITY_TYPES: tuple[SecurityType, ...] = ("common", "preferred", "fund", "adr", "other")

# The longest ticker in either SEC list is 7 characters (2026-10-25).
MAX_LENGTH = 12
_CANONICAL = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_PREFERRED = re.compile(r"^P[A-Z]?$")
# Warrants (WT, WTA), units (UN), rights (RI) and when-issued shares (WI), as the exchange
# list writes them.
_NOT_SHARES = re.compile(r"^(?:WT[A-Z]?|UN|RI|WI)$")


class TickerError(ValueError):
    """Text that can't be a ticker. The message says what a ticker looks like."""


def canonical_ticker(text: str) -> str:
    """The SEC's spelling of a ticker: `brk.b`, `BRK/B` and `BRK-B` all give `BRK-B`.

    Case is ignored; '.', '/' and '-' are the same separator; surrounding parentheses, as on
    some fund-list entries, are dropped.
    """
    value = text.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    value = value.upper().replace(".", "-").replace("/", "-")
    if len(value) > MAX_LENGTH or not _CANONICAL.match(value):
        raise TickerError(
            f"{text!r} is not a ticker: expected letters and digits, with '.', '-' or '/' "
            "before a class suffix, for example BRK-B"
        )
    return value


def ticker_type(ticker: str) -> tuple[SecurityType, str | None]:
    """The security type and share class that a canonical ticker's suffix shows.

    `JPM-PC` and `ETI-P` are preferreds; `VAL-WT`, `KCAC-UN`, `CELG-RI` and `MSGK-WI` are
    warrants, units, rights and when-issued shares (`other`); a one-letter suffix is a share
    class (`BRK-A` is class A common). Other suffixes are `other`; no suffix is `common`.
    """
    if "-" not in ticker:
        return "common", None
    suffix = ticker.rsplit("-", 1)[1]
    if _PREFERRED.match(suffix):
        return "preferred", None
    if _NOT_SHARES.match(suffix):
        return "other", None
    if len(suffix) == 1:
        return "common", suffix
    return "other", None
