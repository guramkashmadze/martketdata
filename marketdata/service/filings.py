"""Filings with plain-language event labels (E3-S6; served by the API and MCP from E5a and E6).

Reads the filings table only; it never calls the SEC (CLAUDE.md). Each filing links to its
index page on sec.gov, the proof of what was filed.
"""

from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import filings
from marketdata.normalize.filing_events import Importance, filing_events
from marketdata.sec import urls
from marketdata.service.errors import InvalidParameter

MAX_LIMIT = 100


class FilingEventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_or_form: str
    label: str
    importance: Importance


class FilingView(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str
    filed_at: date
    accepted_at: datetime | None  # null when the SEC's times for it failed the check (D-057)
    period_of_report: date | None
    url: str  # the filing's index page on sec.gov
    events: tuple[FilingEventView, ...]


async def recent_filings(
    conn: AsyncConnection[Any],
    cik: int,
    *,
    form: str | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[FilingView]:
    """A company's filings, newest first, with their events (hidden ones left out)."""
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidParameter(f"limit must be from 1 to {MAX_LIMIT}, got {limit}")
    rows = await filings.list_for_company(
        conn, cik, forms=[form] if form else None, since=since, limit=limit
    )
    return [
        FilingView(
            accession_no=row.accession_no,
            form=row.form,
            filed_at=row.filed_at,
            accepted_at=row.accepted_at,
            period_of_report=row.period_of_report,
            url=urls.filing_index_page(row.cik, row.accession_no),
            events=tuple(
                FilingEventView(
                    item_or_form=event.item_or_form,
                    label=event.label,
                    importance=event.importance,
                )
                for event in filing_events(row.form, row.items_8k, row.file_number)
            ),
        )
        for row in rows
    ]
