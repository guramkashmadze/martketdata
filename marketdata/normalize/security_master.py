"""The security master from SEC files: current tickers, company details and filer classes
(PLAN.md §9.1-§9.2, D-051 to D-053).

Pure: functions take the bytes of SEC files and stored rows, and return records and the
changes to make. ingest/security_master.py fetches the files (raw store first) and applies
the changes.

- `company_tickers_exchange.json` lists operating companies, ETFs organised as trusts and
  foreign issuers: cik, name, ticker, exchange.
- `company_tickers_mf.json` lists funds and their share classes: cik, seriesId, classId,
  symbol. It has no names, and none of its CIKs is in the exchange list (2026-10-25).

A ticker listed in both files under different CIKs keeps the exchange list's entry (SPCX is
SpaceX there and an old fund series in the fund list). Entries that can't be tickers
(`NONE.`, an empty symbol) are skipped and reported, never guessed.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal, Protocol

from marketdata.normalize.tickers import SecurityType, TickerError, canonical_ticker, ticker_type
from marketdata.normalize.universe import SecurityOverride

ListSource = Literal["exchange", "fund"]
FilerType = Literal["domestic", "foreign_private_issuer", "fund", "other", "unknown"]
FILER_TYPES: tuple[FilerType, ...] = (
    "domestic",
    "foreign_private_issuer",
    "fund",
    "other",
    "unknown",
)
EXCHANGE_FIELDS = ("cik", "name", "ticker", "exchange")
FUND_FIELDS = ("cik", "seriesId", "classId", "symbol")

# Periodic reports, which decide a filer's class (D-052).
DOMESTIC_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A"}
)
FPI_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})
FUND_FORMS = frozenset({"N-CSR", "N-CSR/A", "N-CSRS", "N-CSRS/A", "NPORT-P", "NPORT-P/A"})
# "Commodity Contracts Brokers & Dealers": exchange-traded commodity trusts and pools (GLD,
# IBIT, USO) file 10-K and 10-Q under it, but never report results under 8-K item 2.02.
COMMODITY_POOL_SIC = "6221"
_MMDD = re.compile(r"^(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])$")


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


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    items: tuple[str, ...]  # 8-K items, e.g. ("2.02", "9.01")


@dataclass(frozen=True)
class CompanyDetails:
    """What a company's submissions JSON says about it."""

    cik: int
    name: str
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None  # 'MMDD' as the SEC gives it; not the latest reported year end
    state_of_inc: str | None
    filings: tuple[Filing, ...]  # filings.recent, newest first: at least a year of filings


def parse_submissions(data: bytes) -> CompanyDetails:
    """Company details and recent filings; empty strings become None (MNST's state, a fund's
    SIC)."""
    try:
        document = json.loads(data)
    except ValueError as exc:
        raise SecFileError(f"submissions JSON is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise SecFileError("submissions JSON: expected an object")
    cik, name = document.get("cik"), document.get("name")
    if not (isinstance(cik, str) and cik.isdigit() and int(cik) > 0) or not (
        isinstance(name, str) and name.strip()
    ):
        raise SecFileError(f"submissions JSON has no usable cik and name: {cik!r}, {name!r}")
    filings = document.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise SecFileError(f"submissions JSON for CIK {int(cik)} has no filings.recent")
    return CompanyDetails(
        cik=int(cik),
        name=name.strip(),
        sic_code=_text(document.get("sic")),
        sic_description=_text(document.get("sicDescription")),
        fiscal_year_end=_mmdd(document.get("fiscalYearEnd")),
        state_of_inc=_text(document.get("stateOfIncorporation")),
        filings=_recent_filings(recent, int(cik)),
    )


@dataclass(frozen=True)
class Classification:
    filer_type: FilerType
    reason: str  # the evidence, e.g. "10-Q filed 2026-07-31"


def classify_filer(details: CompanyDetails | None, *, in_fund_list: bool) -> Classification:
    """§9.1 and D-052: the fund list, then the newest periodic report in the recent filings."""
    if in_fund_list:
        return Classification("fund", "listed in company_tickers_mf.json")
    if details is None:
        return Classification("unknown", "submissions not loaded")
    periodic = next(
        (f for f in details.filings if f.form in DOMESTIC_FORMS | FPI_FORMS | FUND_FORMS), None
    )
    if periodic is not None:
        filed = f"{periodic.form} filed {periodic.filed.isoformat()}"
        if periodic.form in FPI_FORMS:
            return Classification("foreign_private_issuer", filed)
        if periodic.form in FUND_FORMS:
            return Classification("fund", filed)
        if details.sic_code == COMMODITY_POOL_SIC and not _reports_results(details):
            return Classification(
                "fund",
                f"{filed}; SIC {COMMODITY_POOL_SIC} and no 8-K item 2.02: an exchange-traded "
                "commodity trust or pool",
            )
        return Classification("domestic", filed)
    six_k = next((f for f in details.filings if f.form in ("6-K", "6-K/A")), None)
    if six_k is not None:
        return Classification("foreign_private_issuer", f"{six_k.form} filed {six_k.filed}")
    return Classification(
        "other", "no 10-K, 10-Q, 20-F, 40-F, 6-K, N-CSR or N-PORT in its recent filings"
    )


def _reports_results(details: CompanyDetails) -> bool:
    return any(f.form in ("8-K", "8-K/A") and "2.02" in f.items for f in details.filings)


class StoredCompany(Protocol):
    """A companies row as the repository returns it."""

    @property
    def cik(self) -> int: ...
    @property
    def name(self) -> str | None: ...
    @property
    def sic_code(self) -> str | None: ...
    @property
    def sic_description(self) -> str | None: ...
    @property
    def fiscal_year_end(self) -> str | None: ...
    @property
    def state_of_inc(self) -> str | None: ...
    @property
    def filer_type(self) -> FilerType: ...
    @property
    def in_universe(self) -> bool: ...


@dataclass(frozen=True)
class CompanyRow:
    """A companies row as a load writes it (updated_at is the database's)."""

    cik: int
    name: str | None
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_inc: str | None
    filer_type: FilerType
    in_universe: bool


@dataclass(frozen=True)
class UniverseCompany:
    cik: int
    details: CompanyDetails | None  # None when its submissions could not be loaded
    classification: Classification


def plan_companies(
    stored: Mapping[int, StoredCompany],
    listed: Mapping[int, ListedCompany],
    universe: Mapping[int, UniverseCompany],
    predecessors: Mapping[int, UniverseCompany] | None = None,
) -> tuple[list[CompanyRow], list[CompanyRow]]:
    """The companies rows to add and to change, so that an unchanged input writes nothing.

    Universe companies take name, details and class from their submissions (D-052, D-053);
    if those could not be loaded, what is stored stays. So do predecessors of universe
    companies (D-068), which stay out of the universe. Other companies take the exchange
    list's name; their details and class stay as stored, except that the fund list makes a
    company a fund. A company that is no longer listed anywhere is kept, out of the universe.
    """
    loaded = {**(predecessors or {}), **universe}
    added: list[CompanyRow] = []
    changed: list[CompanyRow] = []
    for cik in sorted(set(stored) | set(listed) | set(loaded)):
        current = stored.get(cik)
        base = (
            CompanyRow(cik, None, None, None, None, None, "unknown", False)
            if current is None
            else CompanyRow(
                cik,
                current.name,
                current.sic_code,
                current.sic_description,
                current.fiscal_year_end,
                current.state_of_inc,
                current.filer_type,
                current.in_universe,
            )
        )
        row = replace(base, in_universe=cik in universe)
        listing = listed.get(cik)
        member = loaded.get(cik)
        if member is not None and member.details is not None:
            details = member.details
            row = replace(
                row,
                name=details.name,
                sic_code=details.sic_code,
                sic_description=details.sic_description,
                fiscal_year_end=details.fiscal_year_end,
                state_of_inc=details.state_of_inc,
            )
        elif (
            listing is not None
            and listing.name is not None
            and (member is None or row.name is None)
        ):
            row = replace(row, name=listing.name)
        if member is not None and member.classification.filer_type != "unknown":
            row = replace(row, filer_type=member.classification.filer_type)
        elif listing is not None and listing.in_fund_list:
            row = replace(row, filer_type="fund")
        if current is None:
            added.append(row)
        elif row != base:
            changed.append(row)
    return added, changed


class StoredSecurity(Protocol):
    """A current securities row as the repository returns it."""

    @property
    def security_id(self) -> int: ...
    @property
    def cik(self) -> int: ...
    @property
    def ticker(self) -> str: ...
    @property
    def security_type(self) -> SecurityType: ...
    @property
    def exchange(self) -> str | None: ...
    @property
    def share_class(self) -> str | None: ...
    @property
    def valid_from(self) -> date: ...


@dataclass(frozen=True)
class SecurityUpdate:
    """A current row whose type, class or exchange changed; its ticker and CIK did not, unless
    the row was opened on the snapshot's own date (a move that day replaces it in place)."""

    security_id: int
    listing: Listing


@dataclass(frozen=True)
class ClosedSecurity:
    row: StoredSecurity
    moved_to: int | None  # the CIK that lists the ticker now; None when no list has it


@dataclass(frozen=True)
class SecurityChanges:
    """What a snapshot changes in the securities table (E2-S4, D-054)."""

    opened: tuple[Listing, ...]  # valid_from = the snapshot's date
    closed: tuple[ClosedSecurity, ...]  # valid_to = the snapshot's date
    updated: tuple[SecurityUpdate, ...]
    deleted: tuple[StoredSecurity, ...]  # opened on the snapshot's date and gone the same day
    unchanged: int

    @property
    def moved(self) -> tuple[ClosedSecurity, ...]:
        return tuple(closed for closed in self.closed if closed.moved_to is not None)


class SnapshotRefused(Exception):
    """A snapshot would close too many current rows to be trusted (a truncated download)."""


# A snapshot may close at most this share of current rows, but always at least MIN_CLOSES.
MAX_CLOSE_FRACTION = 0.05
MIN_CLOSES = 50


def diff_securities(
    current: Sequence[StoredSecurity], snapshot: Mapping[str, Listing], as_of: date
) -> SecurityChanges:
    """Compare the current rows with a snapshot of the SEC lists taken on `as_of`.

    A ticker no list has any more is closed on `as_of`; a ticker another CIK lists now is
    closed and opened again for that CIK. A row opened on `as_of` itself was never valid for a
    whole day, so it is deleted or changed in place instead of closed (`valid_to` must be
    after `valid_from`).
    """
    by_ticker = {row.ticker: row for row in current}
    opened: list[Listing] = []
    closed: list[ClosedSecurity] = []
    updated: list[SecurityUpdate] = []
    deleted: list[StoredSecurity] = []
    unchanged = 0
    for ticker, listing in sorted(snapshot.items()):
        row = by_ticker.get(ticker)
        if row is None:
            opened.append(listing)
        elif row.cik != listing.cik and row.valid_from < as_of:
            closed.append(ClosedSecurity(row, moved_to=listing.cik))
            opened.append(listing)
        elif row.cik != listing.cik or (row.security_type, row.share_class, row.exchange) != (
            listing.security_type,
            listing.share_class,
            listing.exchange,
        ):
            updated.append(SecurityUpdate(row.security_id, listing))
        else:
            unchanged += 1
    for row in sorted(current, key=lambda row: row.ticker):
        if row.ticker in snapshot:
            continue
        if row.valid_from < as_of:
            closed.append(ClosedSecurity(row, moved_to=None))
        else:
            deleted.append(row)
    return SecurityChanges(tuple(opened), tuple(closed), tuple(updated), tuple(deleted), unchanged)


def check_snapshot(
    current: Sequence[StoredSecurity],
    changes: SecurityChanges,
    as_of: date,
    *,
    force: bool = False,
) -> None:
    """Refuse a snapshot that is older than the stored rows, or that ends more tickers than
    real churn does (unless `force`). Raises SnapshotRefused.

    Each list is judged on its own rows (fund-list tickers are the `fund` ones), so a cut-short
    exchange list can't hide among the fund list's 28,000 rows.
    """
    newest = max((row.valid_from for row in current), default=None)
    if newest is not None and newest > as_of:
        raise SnapshotRefused(
            f"the stored tickers are from {newest}, newer than this snapshot of {as_of}; "
            "nothing was changed"
        )
    if force:
        return
    gone = [closed.row for closed in changes.closed if closed.moved_to is None]
    gone += changes.deleted
    for label, fund in (("exchange list", False), ("fund list", True)):
        total = sum(1 for row in current if (row.security_type == "fund") == fund)
        lost = sum(1 for row in gone if (row.security_type == "fund") == fund)
        limit = max(MIN_CLOSES, int(total * MAX_CLOSE_FRACTION))
        if lost > limit:
            raise SnapshotRefused(
                f"the {label} would end {lost:,} of {total:,} current tickers (the limit is "
                f"{limit:,}); it may have been cut short. Nothing was changed: fetch again later, "
                "or pass --force if the list is right"
            )


def _recent_filings(recent: dict[str, Any], cik: int) -> tuple[Filing, ...]:
    forms, dates = recent.get("form"), recent.get("filingDate")
    items = recent.get("items")
    if not isinstance(forms, list) or not isinstance(dates, list) or len(forms) != len(dates):
        raise SecFileError(f"submissions JSON for CIK {cik}: form and filingDate don't align")
    if not isinstance(items, list) or len(items) != len(forms):
        items = [""] * len(forms)
    filings = []
    for form, filed, item in zip(forms, dates, items, strict=True):
        try:
            filed_on = date.fromisoformat(filed)
        except TypeError, ValueError:
            raise SecFileError(
                f"submissions JSON for CIK {cik}: bad filingDate {filed!r}"
            ) from None
        codes = (
            tuple(code.strip() for code in item.split(",") if code.strip())
            if isinstance(item, str)
            else ()
        )
        filings.append(Filing(str(form), filed_on, codes))
    # The SEC lists the newest first; sorting makes that a guarantee.
    return tuple(sorted(filings, key=lambda filing: filing.filed, reverse=True))


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _mmdd(value: object) -> str | None:
    return value if isinstance(value, str) and _MMDD.match(value) else None
