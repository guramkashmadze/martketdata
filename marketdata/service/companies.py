"""Company lookup (E2): ticker resolution and what data applies. Snapshot and coverage (E5a)
build on these.

Service functions take a database connection and read clean tables only (CLAUDE.md).
"""

from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import companies, securities
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
