"""The filings table (PLAN.md §7, E3-S4): one row per filing and listing company (D-060).

Rows are only ever added; the one value that changes is a missing acceptance time, which a
later file may fill (D-057).
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.filings import AcceptanceFill, FilingRow


class Filing(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: int
    form: str
    filed_at: date
    accepted_at: datetime | None
    period_of_report: date | None
    items_8k: list[str] | None
    primary_doc: str | None
    is_xbrl: bool | None
    file_number: str | None
    discovered_at: datetime


_COLUMNS = """
    accession_no, cik, form, filed_at, accepted_at, period_of_report, items_8k, primary_doc,
    is_xbrl, file_number, discovered_at
"""

# One statement for a whole file (JPMorgan lists 26,000 filings a year). 8-K items travel as
# comma-joined text, since unnest flattens arrays of arrays.
_INSERT_NEW = """
    insert into filings (
      accession_no, cik, form, filed_at, accepted_at, period_of_report, items_8k, primary_doc,
      is_xbrl, file_number
    )
    select accession_no, cik, form, filed_at, accepted_at, period_of_report,
           string_to_array(items, ','), primary_doc, is_xbrl, file_number
    from unnest(
      %(accession_no)s::text[], %(cik)s::bigint[], %(form)s::text[], %(filed_at)s::date[],
      %(accepted_at)s::timestamptz[], %(period_of_report)s::date[], %(items)s::text[],
      %(primary_doc)s::text[], %(is_xbrl)s::boolean[], %(file_number)s::text[]
    ) as listed (
      accession_no, cik, form, filed_at, accepted_at, period_of_report, items, primary_doc,
      is_xbrl, file_number
    )
    on conflict (accession_no, cik) do nothing
    returning accession_no
"""

_ACCEPTANCE = """
    select accession_no, accepted_at from filings
    where cik = %(cik)s and accession_no = any(%(accessions)s)
"""

_FILL_ACCEPTED_AT = """
    update filings set accepted_at = listed.accepted_at
    from unnest(%(accessions)s::text[], %(accepted_at)s::timestamptz[])
      as listed (accession_no, accepted_at)
    where filings.cik = %(cik)s and filings.accession_no = listed.accession_no
      and filings.accepted_at is null
"""

_LIST_FOR_COMPANY = f"""
    select {_COLUMNS} from filings
    where cik = %(cik)s
      and (%(forms)s::text[] is null or form = any(%(forms)s))
      and (%(since)s::date is null or filed_at >= %(since)s)
    order by filed_at desc, accepted_at desc nulls last, accession_no desc
    limit %(limit)s
"""

_COUNT_FOR_COMPANY = "select count(*) from filings where cik = %(cik)s"

_WITH_ITEM = f"""
    select {_COLUMNS} from filings
    where cik = any(%(ciks)s) and form = any(%(forms)s) and %(item)s = any(items_8k)
      and filed_at >= %(since)s
    order by filed_at, accession_no, cik
"""

# A company group's filings of some forms, oldest first; a joint filing listed under two of the
# group's CIKs comes once (E4b-S6's cross-check reads the periodic ones).
_OF_FORMS = f"""
    select distinct on (accession_no) {_COLUMNS} from filings
    where cik = any(%(ciks)s) and form = any(%(forms)s)
    order by accession_no, cik
"""

# E8: the Forms 4 of these issuers, from their own listings (no file number, D-063).
_ISSUER_LISTINGS = f"""
    select distinct on (accession_no) {_COLUMNS} from filings
    where cik = any(%(ciks)s) and form = any(%(forms)s) and file_number is null
      and filed_at >= %(since)s
    order by accession_no, cik
"""

# E5a: every listing of these filings (a joint filing or a 13D is listed under two companies).
_BY_ACCESSION = f"""
    select {_COLUMNS} from filings where accession_no = any(%(accessions)s)
    order by accession_no, cik
"""

# E5b: the newest 8-K with one of these items (a test event's filing).
_NEWEST_WITH_ITEMS = f"""
    select {_COLUMNS} from filings
    where form in ('8-K', '8-K/A') and items_8k && %(items)s::text[]
    order by filed_at desc, accepted_at desc nulls last, accession_no desc, cik
    limit 1
"""

# E5a: a company group's filings, newest first, a page at a time after the last one served
# (filing date, acceptance time, accession number). A filing without an acceptance time sorts
# at the start of its day.
_PAGE = f"""
    select {_COLUMNS} from filings
    where cik = any(%(ciks)s)
      and (%(forms)s::text[] is null or form = any(%(forms)s))
      and (%(subject_forms)s::text[] is null or form <> all(%(subject_forms)s)
           or starts_with(file_number, %(subject_file_number)s))
      and (%(since)s::date is null or filed_at >= %(since)s)
      and (%(after_filed)s::date is null
           or (filed_at, coalesce(accepted_at, filed_at::timestamp at time zone 'UTC'),
               accession_no)
              < (%(after_filed)s, %(after_accepted)s, %(after_accession)s))
    order by filed_at desc, coalesce(accepted_at, filed_at::timestamp at time zone 'UTC') desc,
             accession_no desc, cik
    limit %(limit)s
"""


async def insert_new(conn: AsyncConnection[Any], rows: Sequence[FilingRow]) -> list[str]:
    """Insert listings not stored yet; returns the accession numbers inserted."""
    if not rows:
        return []
    params = {
        "accession_no": [row.accession_no for row in rows],
        "cik": [row.cik for row in rows],
        "form": [row.form for row in rows],
        "filed_at": [row.filed_at for row in rows],
        "accepted_at": [row.accepted_at for row in rows],
        "period_of_report": [row.period_of_report for row in rows],
        "items": [None if row.items_8k is None else ",".join(row.items_8k) for row in rows],
        "primary_doc": [row.primary_doc for row in rows],
        "is_xbrl": [row.is_xbrl for row in rows],
        "file_number": [row.file_number for row in rows],
    }
    async with conn.cursor() as cur:
        await cur.execute(_INSERT_NEW, params)
        return [row[0] for row in await cur.fetchall()]


async def acceptance(
    conn: AsyncConnection[Any], cik: int, accessions: Sequence[str]
) -> dict[str, datetime | None]:
    """The stored acceptance time of each of these listings of `cik` that is stored."""
    async with conn.cursor() as cur:
        await cur.execute(_ACCEPTANCE, {"cik": cik, "accessions": list(accessions)})
        return {accession: accepted for accession, accepted in await cur.fetchall()}


async def fill_accepted_at(
    conn: AsyncConnection[Any], cik: int, fills: Sequence[AcceptanceFill]
) -> int:
    """Set missing acceptance times; a stored time is never changed. Returns how many."""
    if not fills:
        return 0
    params = {
        "cik": cik,
        "accessions": [fill.accession_no for fill in fills],
        "accepted_at": [fill.accepted_at for fill in fills],
    }
    async with conn.cursor() as cur:
        await cur.execute(_FILL_ACCEPTED_AT, params)
        return cur.rowcount


async def list_for_company(
    conn: AsyncConnection[Any],
    cik: int,
    *,
    forms: Sequence[str] | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[Filing]:
    """A company's listings, newest first."""
    params = {
        "cik": cik,
        "forms": list(forms) if forms is not None else None,
        "since": since,
        "limit": limit,
    }
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_LIST_FOR_COMPANY, params)
        return await cur.fetchall()


async def count_for_company(conn: AsyncConnection[Any], cik: int) -> int:
    async with conn.cursor() as cur:
        await cur.execute(_COUNT_FOR_COMPANY, {"cik": cik})
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def with_item(
    conn: AsyncConnection[Any],
    ciks: Sequence[int],
    forms: Sequence[str],
    item: str,
    since: date,
) -> list[Filing]:
    """Listings of these companies with an 8-K item (e.g. 2.02), filed on or after `since`,
    oldest first."""
    params = {"ciks": list(ciks), "forms": list(forms), "item": item, "since": since}
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_WITH_ITEM, params)
        return await cur.fetchall()


async def of_forms(
    conn: AsyncConnection[Any], ciks: Sequence[int], forms: Sequence[str]
) -> list[Filing]:
    """These companies' filings of these forms, each accession once, oldest first."""
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_OF_FORMS, {"ciks": list(ciks), "forms": list(forms)})
        listed = await cur.fetchall()
    return sorted(listed, key=lambda filing: (filing.filed_at, filing.accession_no))


async def issuer_listings(
    conn: AsyncConnection[Any], ciks: Sequence[int], forms: Sequence[str], since: date
) -> list[Filing]:
    """These companies' own listings (no file number) of these forms filed on or after `since`,
    each accession once, oldest first: the Forms 4 about them, not their trades in others."""
    params = {"ciks": list(ciks), "forms": list(forms), "since": since}
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_ISSUER_LISTINGS, params)
        listed = await cur.fetchall()
    return sorted(listed, key=lambda filing: (filing.filed_at, filing.accession_no))


async def listings(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[Filing]:
    """Every listing of these filings, by accession number and CIK."""
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_BY_ACCESSION, {"accessions": list(accessions)})
        return await cur.fetchall()


async def page(
    conn: AsyncConnection[Any],
    ciks: Sequence[int],
    *,
    forms: Sequence[str] | None = None,
    subject_forms: Sequence[str] | None = None,
    subject_file_number: str = "",
    since: date | None = None,
    after: tuple[date, datetime, str] | None = None,
    limit: int = 20,
) -> list[Filing]:
    """These companies' listings, newest first, from after `after` (filing date, acceptance
    time or the start of the filing day in UTC, accession number). Listings of `subject_forms`
    are kept only when their file number starts with `subject_file_number`. A filing listed
    under two of the companies comes twice, next to each other."""
    after_filed, after_accepted, after_accession = after or (None, None, None)
    params = {
        "ciks": list(ciks),
        "forms": list(forms) if forms is not None else None,
        "subject_forms": list(subject_forms) if subject_forms is not None else None,
        "subject_file_number": subject_file_number,
        "since": since,
        "after_filed": after_filed,
        "after_accepted": after_accepted,
        "after_accession": after_accession,
        "limit": limit,
    }
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_PAGE, params)
        return await cur.fetchall()


async def newest_with_items(conn: AsyncConnection[Any], items: Sequence[str]) -> Filing | None:
    """The newest 8-K listing with any of these items."""
    async with conn.cursor(row_factory=class_row(Filing)) as cur:
        await cur.execute(_NEWEST_WITH_ITEMS, {"items": list(items)})
        return await cur.fetchone()
