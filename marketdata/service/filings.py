"""Filings with plain-language event labels (E3-S6; served by the API and the CLI from E5a, the
MCP server from E6).

Reads the filings table only; it never calls the SEC (CLAUDE.md). A company's filings include
its predecessors' (D-068): XOM's history is under Exxon Mobil Corp (34088). Each filing links to
its index page on sec.gov, the proof of what was filed.

A form asked for matches its family (D-139, normalize/form_families.py): `13G` the four 13G
codes, `8-K` its amendments too; the answer names the codes in `matched_forms`. A 13D or 13G
asked for counts only on the listing of the company it is about, as in the list without a
form: JPMorgan's own stakes in other companies aren't JPMorgan's 5% holders.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import filings
from marketdata.db.repositories.filings import Filing
from marketdata.normalize.filing_events import SUBJECT_FILE_NUMBER, Importance, filing_events
from marketdata.normalize.form_families import STAKE_CODES, form_family
from marketdata.sec import urls
from marketdata.service.companies import group_of, resolve_ticker
from marketdata.service.errors import InvalidParameter
from marketdata.service.freshness import company_check
from marketdata.service.pages import (
    BAD_CURSOR,
    DEFAULT_LIMIT,
    check_limit,
    decode_cursor,
    encode_cursor,
)
from marketdata.service.served import Served, newest, sources_of

MAX_LIMIT = 100
# What a company snapshot shows as its latest filings (D-098): annual, quarterly and current
# reports, and 5% stakes in the company, leaving out prospectuses and insider forms (JPMorgan
# files over 26,000 424B2 notes a year). A stake filing counts only on the listing of the
# company it is about (D-061), so a company's own stakes in others are left out, and 13G
# amendments are, as §9.6 hides them.
STAKE_FORMS: tuple[str, ...] = (
    "SC 13D", "SC 13D/A", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G",
)  # fmt: skip
SNAPSHOT_FORMS: tuple[str, ...] = (
    *(
        form + amendment
        for form in ("10-K", "10-KT", "10-Q", "10-QT", "8-K", "20-F", "40-F", "6-K")
        for amendment in ("", "/A")
    ),
    *STAKE_FORMS,
)


class FilingEventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_or_form: str
    label: str
    importance: Importance


class FilingView(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: int  # the listing company: the company, or a predecessor
    form: str
    filed_at: date
    accepted_at: datetime | None  # null when the SEC's times for it failed the check (D-057)
    period_of_report: date | None
    url: str  # the filing's index page on sec.gov
    events: tuple[FilingEventView, ...]


class CompanyFilings(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    # The form codes the asked form matched: its family, amendments and other SEC codes
    # included (D-139); null when no form was asked for.
    matched_forms: tuple[str, ...] | None = None
    filings: tuple[FilingView, ...]  # newest first
    next_cursor: str | None  # pass it as `cursor` for the next page; null on the last


async def recent_filings(
    conn: AsyncConnection[Any],
    ticker: str,
    *,
    form: str | None = None,
    reports_only: bool = False,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Served[CompanyFilings]:
    """A company's filings (its predecessors' too), newest first, with their events (hidden
    ones left out), filed on or after `since`. Filings apply to every filer class. With `form`
    (e.g. 8-K, 13G, 'SCHEDULE 13D/A'), the form's family: its amendments and its other SEC
    codes, or only the family's amendments for an amendment, a 13D or 13G only about the
    company, and a 13G amendment with its hidden label (D-139). Without a form, `reports_only`
    keeps the snapshot's forms: annual, quarterly and current reports and 5% stakes in the
    company (D-098; the MCP tool's default, D-110). A blank form is no form."""
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidParameter(f"limit must be from 1 to {MAX_LIMIT}, got {limit}")
    matched = asked_forms(form)
    security = await resolve_ticker(conn, ticker)
    forms: Sequence[str] | None = matched
    subject_forms: Sequence[str] | None = None if matched is None else sorted(STAKE_CODES)
    if matched is None and reports_only:
        forms, subject_forms = SNAPSHOT_FORMS, STAKE_FORMS
    listed, next_cursor = await filings_page(
        conn,
        await group_of(conn, security.cik),
        forms=forms,
        subject_forms=subject_forms,
        since=since,
        limit=limit,
        cursor=cursor,
    )
    check = await company_check(conn, security.cik)
    return Served(
        data=CompanyFilings(
            ticker=security.ticker,
            cik=security.cik,
            company=security.name,
            matched_forms=matched,
            filings=tuple(filing_view(row, form_asked=matched is not None) for row in listed),
            next_cursor=next_cursor,
        ),
        sources=await sources_of(conn, ((row.cik, row.accession_no) for row in listed)),
        as_of=newest(row.discovered_at for row in listed),
        checked_at=check.at,
        check_overdue=check.overdue,
        quality_flags=(),
    )


async def filings_page(
    conn: AsyncConnection[Any],
    group: Sequence[int],
    *,
    forms: Sequence[str] | None = None,
    subject_forms: Sequence[str] | None = None,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[list[Filing], str | None]:
    """A page of a company group's filings (the company first in `group`), each filing once:
    a filing listed under two of the group's CIKs comes with the company's own listing. Forms
    in `subject_forms` count only on the listing of the company they are about. Returns the
    page and the cursor of the next one."""
    check_limit(limit)
    after = _after(cursor) if cursor else None
    rows = await filings.page(
        conn,
        group,
        forms=forms,
        subject_forms=subject_forms,
        subject_file_number=SUBJECT_FILE_NUMBER,
        since=since,
        after=after,
        limit=(limit + 1) * len(group),
    )
    kept: dict[str, Filing] = {}
    for row in rows:
        if row.accession_no not in kept or row.cik == group[0]:
            kept[row.accession_no] = row
    unique = list(kept.values())
    page = unique[:limit]
    if len(unique) <= limit:
        return page, None
    last = page[-1]
    return page, encode_cursor(
        last.filed_at.isoformat(), _sort_time(last).isoformat(), last.accession_no
    )


def asked_forms(form: str | None) -> tuple[str, ...] | None:
    """The codes a requested form matches (D-139); none for no form or a blank one."""
    if form is None or not form.strip():
        return None
    try:
        return form_family(form)
    except ValueError as exc:
        raise InvalidParameter(str(exc)) from None


def filing_view(row: Filing, *, form_asked: bool = False) -> FilingView:
    """A listing with its events; `form_asked`: listed because its form was asked for, so a
    hidden form label (a 13G amendment's) is shown, as hidden (D-139)."""
    return FilingView(
        accession_no=row.accession_no,
        cik=row.cik,
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
            for event in filing_events(
                row.form, row.items_8k, row.file_number, form_asked=form_asked
            )
        ),
    )


def _sort_time(row: Filing) -> datetime:
    """The acceptance time, or the start of the filing day in UTC (filings.page sorts so)."""
    return row.accepted_at or datetime.combine(row.filed_at, time.min, tzinfo=UTC)


def _after(cursor: str) -> tuple[date, datetime, str]:
    filed, accepted, accession = decode_cursor(cursor, 3)
    try:
        return date.fromisoformat(filed), datetime.fromisoformat(accepted), accession
    except ValueError:
        raise InvalidParameter(BAD_CURSOR) from None
