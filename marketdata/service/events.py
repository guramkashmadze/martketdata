"""What each webhook event says (PLAN.md §9.11; E5b): the object in its body, built from clean
tables with the API's own functions, and the proof page it links to.

- `earnings.verified`: the API's release object (D-097), as `GET /v1/earnings/{accession_no}`
  serves it, each value with its proof.
- `financials.updated`: the company, and the metrics, periods and filings of the rows a facts
  refresh added or changed; the rows themselves come from `/v1/companies/{ticker}/financials`.
- `filing.event`: the company and the filing with its §9.6 labels, as `/filings` lists it.
- `data.changed`: the change as `GET /v1/changes` serves it (E5b-S2).

Objects carry no end-user data (§9.11): tickers, filings and values only.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import filings
from marketdata.normalize.webhooks import (
    DATA_CHANGED,
    EARNINGS_VERIFIED,
    FILING_EVENT,
    FINANCIALS_UPDATED,
)
from marketdata.service.changes import get_change, proof_path
from marketdata.service.companies import company_refs
from marketdata.service.earnings import get_earnings
from marketdata.service.errors import NotFound
from marketdata.service.filings import FilingView, filing_view
from marketdata.service.served import Source, sources_of


class PeriodRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_type: str  # Q, FY or TTM
    period_end: date


class FinancialsUpdate(BaseModel):
    """`financials.updated`: what a facts refresh added or changed for a company."""

    model_config = ConfigDict(frozen=True)

    ticker: str | None  # the company's primary ticker
    cik: int  # the company's, also when a predecessor's rows changed
    company: str | None
    filer_cik: int  # whose rows changed: the company or a predecessor
    new_rows: int  # periods that weren't served before
    changed_values: int  # served values that changed (each also a data.changed event)
    metrics: tuple[str, ...]
    periods: tuple[PeriodRef, ...]  # newest first
    filings: tuple[Source, ...]  # the filings those rows read, newest first


class FilingAnnouncement(BaseModel):
    """`filing.event`: a new filing with a high-importance label (§9.6)."""

    model_config = ConfigDict(frozen=True)

    ticker: str | None
    cik: int  # the company's
    company: str | None
    filing: FilingView


@dataclass(frozen=True)
class EventObject:
    object: BaseModel
    proof_path: str | None  # the object's proof page, e.g. /proof/earnings/<accession>


async def event_object(
    conn: AsyncConnection[Any], event_type: str, subject: str, detail: dict[str, Any]
) -> EventObject:
    """The object an event of this type and subject carries, built from the data now."""
    if event_type == EARNINGS_VERIFIED:
        release = (await get_earnings(conn, subject)).data
        return EventObject(release, f"/proof/earnings/{release.release.accession_no}")
    if event_type == FINANCIALS_UPDATED:
        return EventObject(await financials_update(conn, detail), None)
    if event_type == FILING_EVENT:
        accession_no, cik = subject.rsplit(":", 1)
        return EventObject(await filing_announcement(conn, accession_no, int(cik)), None)
    if event_type == DATA_CHANGED:
        change = (await get_change(conn, int(subject))).data
        return EventObject(change, proof_path(change))
    raise ValueError(f"no object is built for {event_type!r} events")


async def financials_update(conn: AsyncConnection[Any], detail: dict[str, Any]) -> FinancialsUpdate:
    """The update a rebuild wrote down (normalize/webhooks.py MetricsUpdate.detail)."""
    filer = int(detail["cik"])
    company = (await company_refs(conn, [filer]))[filer]
    accessions: list[str] = list(detail.get("filings", []))
    sources = await sources_of(conn, [(filer, accession) for accession in accessions])
    order = {accession: position for position, accession in enumerate(accessions)}
    return FinancialsUpdate(
        ticker=company.ticker,
        cik=company.cik,
        company=company.name,
        filer_cik=filer,
        new_rows=int(detail.get("new", 0)),
        changed_values=int(detail.get("changed", 0)),
        metrics=tuple(detail.get("metrics", [])),
        periods=tuple(
            PeriodRef(
                period_type=period["period_type"],
                period_end=date.fromisoformat(period["period_end"]),
            )
            for period in detail.get("periods", [])
        ),
        filings=tuple(sorted(sources, key=lambda source: order[source.accession_no])),
    )


async def filing_announcement(
    conn: AsyncConnection[Any], accession_no: str, cik: int
) -> FilingAnnouncement:
    listing = next(
        (row for row in await filings.listings(conn, [accession_no]) if row.cik == cik), None
    )
    if listing is None:
        raise NotFound(f"No filing {accession_no} is listed under CIK {cik}.")
    company = (await company_refs(conn, [cik]))[cik]
    return FilingAnnouncement(
        ticker=company.ticker, cik=company.cik, company=company.name, filing=filing_view(listing)
    )
