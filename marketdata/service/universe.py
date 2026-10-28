"""Universe growth (D-035, D-085; E4a-S5): candidates for data/universe.csv by public float.

Reads frame_facts and the security master only. A filer's tickers are its current common
stocks with an exchange; its class is known only when it is a universe company (D-052). The
cover pages' share counts check the floats for scale errors (D-085).
"""

from datetime import date
from typing import Any

from psycopg import AsyncConnection

from marketdata.db.repositories import companies, frame_facts, predecessors, securities
from marketdata.normalize.public_float import (
    COVER_SHARES,
    PUBLIC_FLOAT,
    Filer,
    Suggestion,
    latest_points,
    rank,
)


async def suggest(conn: AsyncConnection[Any], *, today: date, top: int) -> Suggestion | None:
    """The ranking for review; None when no public float is loaded yet."""
    points = await frame_facts.points(conn, *PUBLIC_FLOAT)
    if not points:
        return None
    latest = latest_points(points)
    tickers: dict[int, list[str]] = {}
    for security in await securities.list_current(conn):
        if security.security_type == "common" and security.exchange is not None:
            tickers.setdefault(security.cik, []).append(security.ticker)
    universe = {company.cik for company in await companies.list_in_universe(conn)}
    known = {
        company.cik: company
        for company in await companies.get_many(conn, sorted(set(latest) | universe))
    }
    filers = {
        cik: Filer(
            cik=cik,
            name=company.name if company else None,
            filer_type=company.filer_type if company else "unknown",
            in_universe=company.in_universe if company else False,
            tickers=tuple(sorted(tickers.get(cik, ()))),
        )
        for cik in sorted(set(latest) | universe)
        for company in [known.get(cik)]
    }
    links: dict[int, list[int]] = {}
    for link in await predecessors.list_all(conn):
        links.setdefault(link.cik, []).append(link.predecessor_cik)
    counts = latest_points(await frame_facts.points(conn, *COVER_SHARES))
    shares = {cik: point.value for cik, point in counts.items() if point.value > 0}
    return rank(latest, filers, today=today, top=top, predecessors=links, shares=shares)
