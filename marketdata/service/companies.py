"""Company lookup (E2): ticker resolution and what data applies. Snapshot and coverage (E5a)
build on these.

Service functions take a database connection and read clean tables only (CLAUDE.md).
"""

from datetime import date
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import companies, securities
from marketdata.db.repositories.companies import Company
from marketdata.normalize.security_master import FilerType
from marketdata.normalize.tickers import SecurityType, TickerError, canonical_ticker
from marketdata.service.errors import InvalidParameter, NotFound


class ResolvedSecurity(BaseModel):
    """A current security and the company that lists it."""

    model_config = ConfigDict(frozen=True)

    ticker: str  # the SEC's spelling, e.g. BRK-B
    cik: int
    name: str | None  # null when the company is listed only in the SEC fund list
    filer_type: FilerType
    in_universe: bool
    security_type: SecurityType
    share_class: str | None
    exchange: str | None
    listed_since: date  # the first day our snapshots showed this ticker for this CIK


async def resolve_ticker(conn: AsyncConnection[Any], text: str) -> ResolvedSecurity:
    """The current security for a ticker (PLAN.md §9.2): case is ignored and `.`, `-` and `/`
    are the same separator, so `brk.b`, `BRK-B` and `BRK/B` all give BRK-B.

    Raises InvalidParameter for text that can't be a ticker, and NotFound for a ticker no
    current SEC list has; when the ticker was listed before, the message says by whom.
    """
    try:
        ticker = canonical_ticker(text)
    except TickerError as exc:
        raise InvalidParameter(str(exc)) from None
    current = await securities.find_current(conn, [ticker])
    if not current:
        raise NotFound(await _not_listed(conn, ticker))
    (row,) = current
    company = await companies.get(conn, row.cik)
    if company is None:  # securities.cik references companies
        raise RuntimeError(f"securities row {row.security_id} has no company {row.cik}")
    return ResolvedSecurity(
        ticker=row.ticker,
        cik=row.cik,
        name=company.name,
        filer_type=company.filer_type,
        in_universe=company.in_universe,
        security_type=row.security_type,
        share_class=row.share_class,
        exchange=row.exchange,
        listed_since=row.valid_from,
    )


async def _not_listed(conn: AsyncConnection[Any], ticker: str) -> str:
    previous = await securities.latest(conn, [ticker])
    if not previous or previous[0].valid_to is None:
        return (
            f"No security has the ticker {ticker} in the SEC's lists. Check the spelling; "
            "share classes are written like BRK-B, BRK.B or BRK/B."
        )
    row = previous[0]
    company = await companies.get(conn, row.cik)
    name = f" ({company.name})" if company is not None and company.name else ""
    return (
        f"{ticker} is no longer listed: it was the ticker of CIK {row.cik}{name} until "
        f"{row.valid_to}."
    )


DataKind = Literal["earnings", "fundamentals"]


class Capability(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: DataKind
    applies: bool
    reason: str


class Capabilities(BaseModel):
    """Which data kinds apply to a company, each with the reason (PLAN.md §9.1, D-018)."""

    model_config = ConfigDict(frozen=True)

    cik: int
    filer_type: FilerType
    in_universe: bool
    kinds: tuple[Capability, ...]

    def of(self, kind: DataKind) -> Capability:
        return next(capability for capability in self.kinds if capability.kind == kind)


# Earnings and fundamentals, in that order, for classes they never apply to.
_NOT_APPLICABLE: dict[FilerType, tuple[str, str]] = {
    "fund": (
        "A fund: earnings releases don't apply.",
        "A fund: fundamentals don't apply.",
    ),
    "foreign_private_issuer": (
        "A foreign private issuer: its results come in 6-K reports, which are not covered yet.",
        "A foreign private issuer: its 20-F and 40-F annual reports are not covered yet.",
    ),
    "other": (
        "Files no 10-K or 10-Q, so earnings releases don't apply.",
        "Files no 10-K or 10-Q, so fundamentals don't apply.",
    ),
}


def capabilities_for(company: Company) -> Capabilities:
    """In the MVP, earnings and fundamentals apply to domestic filers in the universe only."""
    if company.filer_type in _NOT_APPLICABLE:
        earnings, fundamentals = _NOT_APPLICABLE[company.filer_type]
        applies = False
    elif not company.in_universe:
        earnings = fundamentals = "Not in the covered universe."
        applies = False
    elif company.filer_type == "unknown":
        earnings = fundamentals = "Not classified yet: its SEC filings could not be loaded."
        applies = False
    else:
        earnings = "A domestic filer in the covered universe: its earnings releases apply."
        fundamentals = "A domestic filer in the covered universe: its 10-K and 10-Q apply."
        applies = True
    return Capabilities(
        cik=company.cik,
        filer_type=company.filer_type,
        in_universe=company.in_universe,
        kinds=(
            Capability(kind="earnings", applies=applies, reason=earnings),
            Capability(kind="fundamentals", applies=applies, reason=fundamentals),
        ),
    )


async def capabilities(conn: AsyncConnection[Any], cik: int) -> Capabilities:
    """Which data kinds apply to the company with this CIK; NotFound if it isn't stored."""
    return capabilities_for(await _company(conn, cik))


class ListedSecurity(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    security_type: SecurityType
    share_class: str | None
    exchange: str | None
    listed_since: date


class CompanyProfile(BaseModel):
    """A company as `marketdata company show` prints it: identity, class, tickers, capabilities."""

    model_config = ConfigDict(frozen=True)

    cik: int
    name: str | None
    filer_type: FilerType
    in_universe: bool
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None  # 'MMDD' as the SEC gives it
    state_of_inc: str | None
    securities: tuple[ListedSecurity, ...]  # current, by ticker
    capabilities: Capabilities


async def company_profile(conn: AsyncConnection[Any], cik: int) -> CompanyProfile:
    company = await _company(conn, cik)
    rows = await securities.list_current_for_cik(conn, cik)
    return CompanyProfile(
        cik=company.cik,
        name=company.name,
        filer_type=company.filer_type,
        in_universe=company.in_universe,
        sic_code=company.sic_code,
        sic_description=company.sic_description,
        fiscal_year_end=company.fiscal_year_end,
        state_of_inc=company.state_of_inc,
        securities=tuple(
            ListedSecurity(
                ticker=row.ticker,
                security_type=row.security_type,
                share_class=row.share_class,
                exchange=row.exchange,
                listed_since=row.valid_from,
            )
            for row in rows
        ),
        capabilities=capabilities_for(company),
    )


async def _company(conn: AsyncConnection[Any], cik: int) -> Company:
    company = await companies.get(conn, cik)
    if company is None:
        raise NotFound(f"No company has CIK {cik} in the security master.")
    return company
