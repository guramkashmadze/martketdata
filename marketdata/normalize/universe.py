"""The universe file and the security overrides file (PLAN.md §9.1, D-050, D-051).

`data/universe.csv` (`ticker,note`) is the only switch for coverage: a company is in the
universe when one of its tickers is listed there, and every row says why. Other tickers of the
same company (GOOG next to GOOGL) come with it. `data/security_overrides.csv`
(`ticker,security_type,source_url`) corrects the security type of tickers whose spelling
misleads (see tickers.py), each with the SEC page that shows the right type.

Both files are checked before anything is fetched, and every problem is reported at once with
its line number.
"""

import csv
import io
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from marketdata.normalize.tickers import (
    SECURITY_TYPES,
    SecurityType,
    TickerError,
    canonical_ticker,
)

UNIVERSE_COLUMNS = ("ticker", "note")
OVERRIDE_COLUMNS = ("ticker", "security_type", "source_url")
_SECURITY_TYPES: dict[str, SecurityType] = {name: name for name in SECURITY_TYPES}


class DataFileError(ValueError):
    """A hand-maintained data file has problems; each one names its line."""

    def __init__(self, source: str, problems: Sequence[str]) -> None:
        super().__init__(source, tuple(problems))
        self.source = source
        self.problems = tuple(problems)

    def __str__(self) -> str:
        return f"{self.source}:\n" + "\n".join(f"  - {problem}" for problem in self.problems)


@dataclass(frozen=True)
class UniverseEntry:
    line: int
    ticker: str  # canonical
    note: str


@dataclass(frozen=True)
class SecurityOverride:
    line: int
    ticker: str  # canonical
    security_type: SecurityType
    source_url: str


def parse_universe(text: str, source: str = "data/universe.csv") -> list[UniverseEntry]:
    problems: list[str] = []
    entries: list[UniverseEntry] = []
    first_line: dict[str, int] = {}
    for line, row in _rows(text, UNIVERSE_COLUMNS, source, problems):
        try:
            ticker = canonical_ticker(row["ticker"])
        except TickerError as exc:
            problems.append(f"line {line}: {exc}")
            continue
        if ticker in first_line:
            problems.append(f"line {line}: {ticker} is already listed on line {first_line[ticker]}")
            continue
        first_line[ticker] = line
        if not row["note"]:
            problems.append(f"line {line}: {ticker} has no note; say why it is in the universe")
            continue
        entries.append(UniverseEntry(line, ticker, row["note"]))
    if not problems and not entries:
        problems.append("the file lists no tickers")
    if problems:
        raise DataFileError(source, problems)
    return entries


def parse_security_overrides(
    text: str, source: str = "data/security_overrides.csv"
) -> list[SecurityOverride]:
    """The overrides; a file with only its header means none."""
    problems: list[str] = []
    overrides: list[SecurityOverride] = []
    first_line: dict[str, int] = {}
    for line, row in _rows(text, OVERRIDE_COLUMNS, source, problems):
        try:
            ticker = canonical_ticker(row["ticker"])
        except TickerError as exc:
            problems.append(f"line {line}: {exc}")
            continue
        if ticker in first_line:
            problems.append(f"line {line}: {ticker} is already listed on line {first_line[ticker]}")
            continue
        first_line[ticker] = line
        security_type = _SECURITY_TYPES.get(row["security_type"])
        if security_type is None:
            problems.append(
                f"line {line}: security_type {row['security_type']!r} for {ticker} must be one "
                f"of {', '.join(SECURITY_TYPES)}"
            )
            continue
        if not row["source_url"].startswith("https://"):
            problems.append(
                f"line {line}: {ticker} needs a source_url: the https link to the SEC page that "
                "shows its type"
            )
            continue
        overrides.append(SecurityOverride(line, ticker, security_type, row["source_url"]))
    if problems:
        raise DataFileError(source, problems)
    return overrides


@dataclass(frozen=True)
class ClosedTicker:
    """The latest stored row of a ticker that no current listing has (E2-S4)."""

    cik: int
    valid_to: date


@dataclass(frozen=True)
class UniverseMatch:
    matched: tuple[tuple[UniverseEntry, int], ...]  # entry and the CIK listing it now
    closed: tuple[tuple[UniverseEntry, ClosedTicker], ...]  # no longer listed; CIK from history
    unknown: tuple[UniverseEntry, ...]  # neither listed nor ever stored

    @property
    def ciks(self) -> list[int]:
        """The universe companies, in file order."""
        found = [cik for _, cik in self.matched] + [closed.cik for _, closed in self.closed]
        return list(dict.fromkeys(found))

    @property
    def shared(self) -> dict[int, list[str]]:
        """Companies listed through more than one ticker, e.g. both GOOGL and GOOG."""
        tickers: dict[int, list[str]] = {}
        for entry, cik in self.matched:
            tickers.setdefault(cik, []).append(entry.ticker)
        for entry, closed in self.closed:
            tickers.setdefault(closed.cik, []).append(entry.ticker)
        return {cik: names for cik, names in tickers.items() if len(names) > 1}


def match_universe(
    entries: Sequence[UniverseEntry],
    listed: Mapping[str, int],
    closed: Mapping[str, ClosedTicker] | None = None,
) -> UniverseMatch:
    """Match universe tickers to companies: current listings first, then stored history.

    `listed` maps canonical tickers to the CIK that lists them now; `closed` holds the latest
    stored row of tickers that are no longer listed. A universe ticker found only in history
    keeps its company in the universe until the file is updated.
    """
    closed = closed or {}
    matched: list[tuple[UniverseEntry, int]] = []
    found_closed: list[tuple[UniverseEntry, ClosedTicker]] = []
    unknown: list[UniverseEntry] = []
    for entry in entries:
        if entry.ticker in listed:
            matched.append((entry, listed[entry.ticker]))
        elif entry.ticker in closed:
            found_closed.append((entry, closed[entry.ticker]))
        else:
            unknown.append(entry)
    return UniverseMatch(tuple(matched), tuple(found_closed), tuple(unknown))


def _rows(
    text: str, columns: tuple[str, ...], source: str, problems: list[str]
) -> Iterator[tuple[int, dict[str, str]]]:
    """Data rows with their line numbers, in file order; malformed rows go to `problems`.

    A wrong header stops at once: nothing else in the file can be read without it.
    """
    reader = csv.reader(io.StringIO(text.removeprefix("\ufeff")))
    header = next(reader, None)
    if header is None or tuple(name.strip() for name in header) != columns:
        found = ",".join(header) if header else "nothing"
        raise DataFileError(
            source, [f"line 1: the header must be {','.join(columns)}, found {found}"]
        )
    for values in reader:
        if not any(value.strip() for value in values):
            continue
        if len(values) != len(columns):
            problems.append(
                f"line {reader.line_num}: expected {len(columns)} fields "
                f"({','.join(columns)}), found {len(values)}; quote a value that has a comma"
            )
            continue
        yield reader.line_num, dict(zip(columns, (value.strip() for value in values), strict=True))
