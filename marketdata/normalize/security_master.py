"""SEC ticker lists into one snapshot of current tickers (PLAN.md §9.2, D-051).

Pure: functions take the bytes of SEC files and return records. ingest/security_master.py
fetches the files (raw store first) and stores the result.

- `company_tickers_exchange.json` lists operating companies, ETFs organised as trusts and
  foreign issuers: cik, name, ticker, exchange.
- `company_tickers_mf.json` lists funds and their share classes: cik, seriesId, classId,
  symbol. It has no names, and none of its CIKs is in the exchange list (2026-10-25).

A ticker listed in both files under different CIKs keeps the exchange list's entry (SPCX is
SpaceX there and an old fund series in the fund list). Entries that can't be tickers
(`NONE.`, an empty symbol) are skipped and reported, never guessed.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from marketdata.normalize.tickers import SecurityType, TickerError, canonical_ticker, ticker_type
from marketdata.normalize.universe import SecurityOverride

ListSource = Literal["exchange", "fund"]
EXCHANGE_FIELDS = ("cik", "name", "ticker", "exchange")
FUND_FIELDS = ("cik", "seriesId", "classId", "symbol")


class SecFileError(ValueError):
    """An SEC file is not in the shape this code expects, so nothing is loaded from it."""


@dataclass(frozen=True)
class ExchangeRow:
    cik: int
    name: str
    ticker: str  # as the SEC wrote it
    exchange: str | None


@dataclass(frozen=True)
class FundRow:
    cik: int
    series_id: str | None
    class_id: str | None
    symbol: str  # as the SEC wrote it


@dataclass(frozen=True)
class Listing:
    """A current ticker: who lists it and what kind of security it is."""

    ticker: str  # canonical
    cik: int
    security_type: SecurityType
    share_class: str | None
    exchange: str | None
    source: ListSource


@dataclass(frozen=True)
class ListedCompany:
    cik: int
    name: str | None  # from the exchange list; the fund list has no names (D-053)
    in_fund_list: bool


@dataclass(frozen=True)
class SkippedEntry:
    source: ListSource
    cik: int
    text: str  # as the SEC wrote it
    reason: str


@dataclass(frozen=True)
class Collision:
    """A ticker that two CIKs list; `kept` is the listing that was stored."""

    ticker: str
    kept: Listing
    dropped_cik: int
    dropped_source: ListSource


@dataclass(frozen=True)
class TickerSnapshot:
    listings: dict[str, Listing]  # by canonical ticker
    companies: dict[int, ListedCompany]  # every CIK in either list
    skipped: tuple[SkippedEntry, ...]
    collisions: tuple[Collision, ...]
    unused_overrides: tuple[SecurityOverride, ...]  # overrides for tickers no list has
    exchange_rows: int
    fund_rows: int


def parse_exchange_list(data: bytes) -> list[ExchangeRow]:
    rows = []
    for index, row in _table(data, EXCHANGE_FIELDS, "company_tickers_exchange.json"):
        cik, name, ticker, exchange = (row[field] for field in EXCHANGE_FIELDS)
        if not (
            _is_cik(cik)
            and isinstance(name, str)
            and isinstance(ticker, str)
            and (exchange is None or isinstance(exchange, str))
        ):
            raise SecFileError(f"company_tickers_exchange.json row {index} is malformed: {row}")
        rows.append(ExchangeRow(cik, name, ticker, exchange or None))
    return rows


def parse_fund_list(data: bytes) -> list[FundRow]:
    rows = []
    for index, row in _table(data, FUND_FIELDS, "company_tickers_mf.json"):
        cik, series_id, class_id, symbol = (row[field] for field in FUND_FIELDS)
        if not (
            _is_cik(cik)
            and isinstance(symbol, str)
            and (series_id is None or isinstance(series_id, str))
            and (class_id is None or isinstance(class_id, str))
        ):
            raise SecFileError(f"company_tickers_mf.json row {index} is malformed: {row}")
        rows.append(FundRow(cik, series_id or None, class_id or None, symbol))
    return rows


def build_snapshot(
    exchange: Sequence[ExchangeRow],
    fund: Sequence[FundRow],
    overrides: Sequence[SecurityOverride] = (),
) -> TickerSnapshot:
    """One listing per ticker. The exchange list wins collisions; overrides apply last."""
    listings: dict[str, Listing] = {}
    companies: dict[int, ListedCompany] = {}
    skipped: list[SkippedEntry] = []
    collisions: list[Collision] = []

    for row in exchange:
        companies.setdefault(row.cik, ListedCompany(row.cik, row.name, in_fund_list=False))
        try:
            ticker = canonical_ticker(row.ticker)
        except TickerError:
            skipped.append(SkippedEntry("exchange", row.cik, row.ticker, "not a ticker"))
            continue
        security_type, share_class = ticker_type(ticker)
        listing = Listing(ticker, row.cik, security_type, share_class, row.exchange, "exchange")
        _add(listings, collisions, listing)

    for fund_row in fund:
        known = companies.get(fund_row.cik)
        companies[fund_row.cik] = ListedCompany(
            fund_row.cik, known.name if known else None, in_fund_list=True
        )
        try:
            ticker = canonical_ticker(fund_row.symbol)
        except TickerError:
            reason = "empty symbol" if not fund_row.symbol.strip() else "not a ticker"
            skipped.append(SkippedEntry("fund", fund_row.cik, fund_row.symbol, reason))
            continue
        _add(listings, collisions, Listing(ticker, fund_row.cik, "fund", None, None, "fund"))

    unused: list[SecurityOverride] = []
    for override in overrides:
        target = listings.get(override.ticker)
        if target is None:
            unused.append(override)
        else:
            listings[override.ticker] = replace(target, security_type=override.security_type)

    return TickerSnapshot(
        listings=listings,
        companies=companies,
        skipped=tuple(skipped),
        collisions=tuple(collisions),
        unused_overrides=tuple(unused),
        exchange_rows=len(exchange),
        fund_rows=len(fund),
    )


def _add(listings: dict[str, Listing], collisions: list[Collision], listing: Listing) -> None:
    kept = listings.get(listing.ticker)
    if kept is None:
        listings[listing.ticker] = listing
    elif kept.cik == listing.cik:
        # The same company in both lists: a fund that also has an exchange listing.
        if listing.source == "fund":
            listings[listing.ticker] = replace(kept, security_type="fund", share_class=None)
    else:
        collisions.append(Collision(listing.ticker, kept, listing.cik, listing.source))


def _table(data: bytes, fields: tuple[str, ...], name: str) -> list[tuple[int, dict[str, Any]]]:
    """Rows of the SEC's `{"fields": [...], "data": [[...], ...]}` layout, keyed by field."""
    try:
        document = json.loads(data)
    except ValueError as exc:
        raise SecFileError(f"{name} is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise SecFileError(f"{name}: expected an object with 'fields' and 'data'")
    names, rows = document.get("fields"), document.get("data")
    if not isinstance(names, list) or not isinstance(rows, list):
        raise SecFileError(f"{name}: expected an object with 'fields' and 'data'")
    missing = [field for field in fields if field not in names]
    if missing:
        raise SecFileError(f"{name} has no field {', '.join(missing)} (fields: {names})")
    if not rows:
        # An empty list would close every ticker; a real list is never empty.
        raise SecFileError(f"{name} lists no rows")
    table = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(names):
            raise SecFileError(f"{name} row {index} does not match the fields: {row}")
        table.append((index, dict(zip(names, row, strict=True))))
    return table


def _is_cik(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
