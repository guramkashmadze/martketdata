"""What every data service function returns (PLAN.md §10.1): the data, the filings it came from,
how fresh it is and the quality flags on its values. The REST API wraps it in the envelope with
the disclaimer (api/envelope.py); the MCP server (E6) returns the same content.

Sources are the filings behind the served values, newest first, each with its form, filing date
and folder on sec.gov. The form and date come from the filings table, or, for filings older
than the five-year filings backfill, from the facts they reported (D-096). `as_of` is when the
newest row used was last written: it moves when a value, flag or proof changes.

An answer about a company, a comparison or a 13F manager also says when the SEC was last checked
for the data it depends on (`checked_at`) and whether that check is overdue: more than a day ago,
or never (`check_overdue`, D-135; service/freshness.py). Other answers (lookups, feeds, coverage,
the status report) depend on no one watch and write neither field.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, WithJsonSchema

from marketdata.db.repositories import filings, xbrl_facts
from marketdata.sec import urls

DISCLAIMER = "Information from public filings. Not investment advice."

# A served value: a Decimal, written as a JSON number with every digit (D-096). Pydantic's
# schema would call it a string; the API reference says what clients get.
type Number = Annotated[Decimal, WithJsonSchema({"type": "number"}, mode="serialization")]


class Source(BaseModel):
    """A filing a served value came from."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str | None
    filed_at: date | None
    url: str  # the filing's folder on sec.gov


class FlagRef(BaseModel):
    """A quality flag (§9.5) on one served value."""

    model_config = ConfigDict(frozen=True)

    code: str
    metric: str  # the metric, or the earnings slot
    period_end: date | None
    ticker: str | None  # the company's, since a feed or a comparison holds several


class Served[T](BaseModel):
    model_config = ConfigDict(frozen=True)

    data: T
    sources: tuple[Source, ...]
    as_of: datetime | None  # UTC: when the newest row used was last written
    # D-135: when the SEC was last checked for the data this depends on (null: never), and
    # whether that was more than a day ago. Both None: the answer depends on no watch, and the
    # envelope leaves them out (checked_fields).
    checked_at: datetime | None = None
    check_overdue: bool | None = None
    quality_flags: tuple[FlagRef, ...]


CHECK_FIELDS = ("checked_at", "check_overdue")


def checked_fields(written: dict[str, Any]) -> dict[str, Any]:
    """An envelope or answer as written, without the check fields when it depends on no watch."""
    if written.get("check_overdue") is None:
        return {name: value for name, value in written.items() if name not in CHECK_FIELDS}
    return written


async def sources_of(
    conn: AsyncConnection[Any], refs: Iterable[tuple[int, str]]
) -> tuple[Source, ...]:
    """The filings these (CIK, accession number) pairs name, each once, newest first. A filing
    listed under several companies links to the folder of the CIK named first."""
    wanted: dict[str, int] = {}
    for cik, accession in refs:
        wanted.setdefault(accession, cik)
    if not wanted:
        return ()
    found: dict[str, Source] = {}
    for listing in await filings.listings(conn, list(wanted)):
        if listing.accession_no not in found or listing.cik == wanted[listing.accession_no]:
            found[listing.accession_no] = Source(
                accession_no=listing.accession_no,
                form=listing.form,
                filed_at=listing.filed_at,
                url=urls.filing_folder(wanted[listing.accession_no], listing.accession_no),
            )
    missing = [accession for accession in wanted if accession not in found]
    for fact in await xbrl_facts.filings_of_facts(conn, missing) if missing else []:
        found[fact.accession_no] = Source(
            accession_no=fact.accession_no,
            form=fact.form,
            filed_at=fact.filed_at,
            url=urls.filing_folder(wanted[fact.accession_no], fact.accession_no),
        )
    for accession, cik in wanted.items():
        if accession not in found:  # neither listed nor named by a fact: say so with nulls
            found[accession] = Source(
                accession_no=accession,
                form=None,
                filed_at=None,
                url=urls.filing_folder(cik, accession),
            )
    return merge_sources(found.values())


def merge_sources(*groups: Iterable[Source]) -> tuple[Source, ...]:
    """Sources of several parts, each filing once, newest first."""
    kept = {source.accession_no: source for group in groups for source in group}
    return tuple(
        sorted(
            kept.values(),
            key=lambda source: (source.filed_at or date.min, source.accession_no),
            reverse=True,
        )
    )


def newest(moments: Iterable[datetime | None]) -> datetime | None:
    """The latest of these times in UTC, or None."""
    known = [moment.astimezone(UTC) for moment in moments if moment is not None]
    return max(known, default=None)


def unique_flags(flags: Iterable[FlagRef]) -> tuple[FlagRef, ...]:
    return tuple(dict.fromkeys(flags))
