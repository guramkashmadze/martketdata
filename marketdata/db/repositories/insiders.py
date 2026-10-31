"""The insider tables (PLAN.md §7, E8): Form 4 filings as parsed, their owners and trades.

A filing is stored as filed and rewritten only by a new parser version; whether a line is
served is decided apart, by the 4/A that supersedes it or the earlier copy of a filing made
twice (`superseded_by`, E8-S3).
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.form4 import Line, Owner
from marketdata.normalize.insiders import FilingRecord, LineKey, SupersedeReason, Supersession


class StoredFiling(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    issuer_cik: int
    form: str
    filed_at: date
    period_of_report: date
    original_filed_at: date | None
    owner_ciks: list[int]
    rule_10b5_1: bool | None
    not_subject_to_section16: bool | None
    doc_name: str
    raw_key: str
    schema_version: str | None
    footnotes: dict[str, str]
    remarks: str | None
    method: str
    extractor_version: str
    parsed_at: datetime


class StoredOwner(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    owner_cik: int
    position: int
    owner_name: str
    is_director: bool
    is_officer: bool
    officer_title: str | None
    is_ten_pct_owner: bool
    is_other: bool
    other_text: str | None


class StoredLine(BaseModel):
    """A line as stored; `supersession` says what supersedes it, if anything (E8-S3)."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    line_no: int
    issuer_cik: int
    is_derivative: bool
    security_title: str | None
    trade_date: date
    code: str
    acquired_disposed: str | None
    shares: Decimal | None
    price: Decimal | None
    owned_after: Decimal | None
    direct_indirect: str | None
    nature_of_ownership: str | None
    timeliness: str | None
    exercise_price: Decimal | None
    exercise_date: date | None
    expiration_date: date | None
    underlying_title: str | None
    underlying_shares: Decimal | None
    footnotes: dict[str, list[str]]
    method: str
    extractor_version: str
    superseded_by: str | None
    superseded_line: int | None
    superseded_reason: str | None


_FILING_COLUMNS = """
    accession_no, issuer_cik, form, filed_at, period_of_report, original_filed_at, owner_ciks,
    rule_10b5_1, not_subject_to_section16, doc_name, raw_key, schema_version, footnotes,
    remarks, method, extractor_version, parsed_at
"""
_OWNER_COLUMNS = """
    accession_no, owner_cik, position, owner_name, is_director, is_officer, officer_title,
    is_ten_pct_owner, is_other, other_text
"""
_LINE_COLUMNS = """
    accession_no, line_no, issuer_cik, is_derivative, security_title, trade_date, code,
    acquired_disposed, shares, price, owned_after, direct_indirect, nature_of_ownership,
    timeliness, exercise_price, exercise_date, expiration_date, underlying_title,
    underlying_shares, footnotes, method, extractor_version, superseded_by, superseded_line,
    superseded_reason
"""

_UPSERT_FILING = """
    insert into insider_filings (
      accession_no, issuer_cik, form, filed_at, period_of_report, original_filed_at,
      owner_ciks, rule_10b5_1, not_subject_to_section16, doc_name, raw_key, schema_version,
      footnotes, remarks, method, extractor_version
    ) values (
      %(accession_no)s, %(issuer_cik)s, %(form)s, %(filed_at)s, %(period_of_report)s,
      %(original_filed_at)s, %(owner_ciks)s, %(rule_10b5_1)s, %(not_subject_to_section16)s,
      %(doc_name)s, %(raw_key)s, %(schema_version)s, %(footnotes)s, %(remarks)s, %(method)s,
      %(extractor_version)s
    )
    on conflict (accession_no) do update set
      issuer_cik = excluded.issuer_cik, form = excluded.form, filed_at = excluded.filed_at,
      period_of_report = excluded.period_of_report,
      original_filed_at = excluded.original_filed_at, owner_ciks = excluded.owner_ciks,
      rule_10b5_1 = excluded.rule_10b5_1,
      not_subject_to_section16 = excluded.not_subject_to_section16,
      doc_name = excluded.doc_name, raw_key = excluded.raw_key,
      schema_version = excluded.schema_version, footnotes = excluded.footnotes,
      remarks = excluded.remarks, method = excluded.method,
      extractor_version = excluded.extractor_version, parsed_at = now()
"""
_UPSERT_OWNER = """
    insert into insider_owners (
      accession_no, owner_cik, position, owner_name, is_director, is_officer, officer_title,
      is_ten_pct_owner, is_other, other_text
    ) values (
      %(accession_no)s, %(owner_cik)s, %(position)s, %(owner_name)s, %(is_director)s,
      %(is_officer)s, %(officer_title)s, %(is_ten_pct_owner)s, %(is_other)s, %(other_text)s
    )
    on conflict (accession_no, owner_cik) do update set
      position = excluded.position, owner_name = excluded.owner_name,
      is_director = excluded.is_director, is_officer = excluded.is_officer,
      officer_title = excluded.officer_title, is_ten_pct_owner = excluded.is_ten_pct_owner,
      is_other = excluded.is_other, other_text = excluded.other_text
"""
_DELETE_OTHER_OWNERS = """
    delete from insider_owners
    where accession_no = %(accession_no)s and owner_cik <> all(%(owner_ciks)s)
"""
# A line's values as filed; whether it is superseded is kept (E8-S3 decides it apart).
_UPSERT_LINE = """
    insert into insider_transactions (
      accession_no, line_no, issuer_cik, is_derivative, security_title, trade_date, code,
      acquired_disposed, shares, price, owned_after, direct_indirect, nature_of_ownership,
      timeliness, exercise_price, exercise_date, expiration_date, underlying_title,
      underlying_shares, footnotes, method, extractor_version
    ) values (
      %(accession_no)s, %(line_no)s, %(issuer_cik)s, %(is_derivative)s, %(security_title)s,
      %(trade_date)s, %(code)s, %(acquired_disposed)s, %(shares)s, %(price)s, %(owned_after)s,
      %(direct_indirect)s, %(nature_of_ownership)s, %(timeliness)s, %(exercise_price)s,
      %(exercise_date)s, %(expiration_date)s, %(underlying_title)s, %(underlying_shares)s,
      %(footnotes)s, %(method)s, %(extractor_version)s
    )
    on conflict (accession_no, line_no) do update set
      issuer_cik = excluded.issuer_cik, is_derivative = excluded.is_derivative,
      security_title = excluded.security_title, trade_date = excluded.trade_date,
      code = excluded.code, acquired_disposed = excluded.acquired_disposed,
      shares = excluded.shares, price = excluded.price, owned_after = excluded.owned_after,
      direct_indirect = excluded.direct_indirect,
      nature_of_ownership = excluded.nature_of_ownership, timeliness = excluded.timeliness,
      exercise_price = excluded.exercise_price, exercise_date = excluded.exercise_date,
      expiration_date = excluded.expiration_date, underlying_title = excluded.underlying_title,
      underlying_shares = excluded.underlying_shares, footnotes = excluded.footnotes,
      method = excluded.method, extractor_version = excluded.extractor_version
"""
_DELETE_LINES_AFTER = """
    delete from insider_transactions where accession_no = %(accession_no)s and line_no > %(last)s
    returning line_no
"""

_GET_FILING = f"select {_FILING_COLUMNS} from insider_filings where accession_no = %(a)s"
# E8-S3: an insider group, the filings the same owners made about one issuer.
_GROUP = f"""
    select {_FILING_COLUMNS} from insider_filings
    where issuer_cik = %(issuer_cik)s and owner_ciks = %(owner_ciks)s::bigint[]
    order by filed_at, accession_no
"""
_SET_SUPERSEDED = """
    update insider_transactions
    set superseded_by = %(by)s, superseded_line = %(by_line)s, superseded_reason = %(reason)s
    where accession_no = %(accession_no)s and line_no = %(line_no)s
"""
_FILINGS = f"""
    select {_FILING_COLUMNS} from insider_filings where accession_no = any(%(accessions)s)
    order by filed_at, accession_no
"""
_OWNERS = f"""
    select {_OWNER_COLUMNS} from insider_owners where accession_no = any(%(accessions)s)
    order by accession_no, position
"""
_LINES = f"""
    select {_LINE_COLUMNS} from insider_transactions where accession_no = any(%(accessions)s)
    order by accession_no, line_no
"""


async def store(
    conn: AsyncConnection[Any], record: FilingRecord, *, method: str, version: str
) -> list[int]:
    """Store a parsed filing, its owners and its lines as filed; a line a parser version no
    longer finds is deleted. Returns the numbers of the lines deleted."""
    form4 = record.form4
    async with conn.cursor() as cur:
        await cur.execute(
            _UPSERT_FILING,
            {
                "accession_no": record.accession_no,
                "issuer_cik": form4.issuer_cik,
                "form": form4.document_type,
                "filed_at": record.filed_at,
                "period_of_report": form4.period_of_report,
                "original_filed_at": form4.original_filed_at,
                "owner_ciks": list(record.owner_ciks),
                "rule_10b5_1": form4.rule_10b5_1,
                "not_subject_to_section16": form4.not_subject_to_section16,
                "doc_name": record.doc_name,
                "raw_key": record.raw_key,
                "schema_version": form4.schema_version,
                "footnotes": Jsonb(dict(form4.footnotes)),
                "remarks": form4.remarks,
                "method": method,
                "extractor_version": version,
            },
        )
        for position, owner in enumerate(form4.owners, start=1):
            await cur.execute(_UPSERT_OWNER, _owner(record.accession_no, position, owner))
        await cur.execute(
            _DELETE_OTHER_OWNERS,
            {"accession_no": record.accession_no, "owner_ciks": list(record.owner_ciks)},
        )
        for line in form4.lines:
            await cur.execute(
                _UPSERT_LINE, _line(record.accession_no, form4.issuer_cik, line, method, version)
            )
        await cur.execute(
            _DELETE_LINES_AFTER,
            {"accession_no": record.accession_no, "last": len(form4.lines)},
        )
        return sorted(int(row[0]) for row in await cur.fetchall())


async def group_filings(
    conn: AsyncConnection[Any], issuer_cik: int, owner_ciks: Sequence[int]
) -> list[StoredFiling]:
    """Every filing the same owners (their sorted CIKs) made about one issuer, oldest first."""
    async with conn.cursor(row_factory=class_row(StoredFiling)) as cur:
        await cur.execute(_GROUP, {"issuer_cik": issuer_cik, "owner_ciks": sorted(owner_ciks)})
        return await cur.fetchall()


async def set_superseded(
    conn: AsyncConnection[Any], updates: Sequence[tuple[LineKey, Supersession | None]]
) -> None:
    """Mark lines superseded (or served again, with None)."""
    async with conn.cursor() as cur:
        await cur.executemany(
            _SET_SUPERSEDED,
            [
                {
                    "accession_no": accession_no,
                    "line_no": line_no,
                    "by": supersession.by if supersession else None,
                    "by_line": supersession.by_line if supersession else None,
                    "reason": supersession.reason if supersession else None,
                }
                for (accession_no, line_no), supersession in updates
            ],
        )


async def get_filing(conn: AsyncConnection[Any], accession_no: str) -> StoredFiling | None:
    async with conn.cursor(row_factory=class_row(StoredFiling)) as cur:
        await cur.execute(_GET_FILING, {"a": accession_no})
        return await cur.fetchone()


async def filings(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredFiling]:
    """These filings, oldest first."""
    async with conn.cursor(row_factory=class_row(StoredFiling)) as cur:
        await cur.execute(_FILINGS, {"accessions": list(accessions)})
        return await cur.fetchall()


async def owners(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredOwner]:
    """The reporting owners of these filings, in each filing's order."""
    async with conn.cursor(row_factory=class_row(StoredOwner)) as cur:
        await cur.execute(_OWNERS, {"accessions": list(accessions)})
        return await cur.fetchall()


async def lines(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredLine]:
    """Every line of these filings, superseded or not, by filing and line."""
    async with conn.cursor(row_factory=class_row(StoredLine)) as cur:
        await cur.execute(_LINES, {"accessions": list(accessions)})
        return await cur.fetchall()


def _owner(accession_no: str, position: int, owner: Owner) -> dict[str, Any]:
    return {
        "accession_no": accession_no,
        "owner_cik": owner.cik,
        "position": position,
        "owner_name": owner.name,
        "is_director": owner.is_director,
        "is_officer": owner.is_officer,
        "officer_title": owner.officer_title,
        "is_ten_pct_owner": owner.is_ten_pct_owner,
        "is_other": owner.is_other,
        "other_text": owner.other_text,
    }


def _line(
    accession_no: str, issuer_cik: int, line: Line, method: str, version: str
) -> dict[str, Any]:
    return {
        "accession_no": accession_no,
        "line_no": line.line_no,
        "issuer_cik": issuer_cik,
        "is_derivative": line.is_derivative,
        "security_title": line.security_title,
        "trade_date": line.trade_date,
        "code": line.code,
        "acquired_disposed": line.acquired_disposed,
        "shares": line.shares,
        "price": line.price,
        "owned_after": line.owned_after,
        "direct_indirect": line.direct_indirect,
        "nature_of_ownership": line.nature_of_ownership,
        "timeliness": line.timeliness,
        "exercise_price": line.exercise_price,
        "exercise_date": line.exercise_date,
        "expiration_date": line.expiration_date,
        "underlying_title": line.underlying_title,
        "underlying_shares": line.underlying_shares,
        "footnotes": Jsonb({name: list(ids) for name, ids in line.footnote_ids.items()}),
        "method": method,
        "extractor_version": version,
    }


def supersession_of(line: StoredLine) -> Supersession | None:
    """What supersedes a stored line; None while it is served."""
    if line.superseded_by is None or line.superseded_reason not in ("amendment", "duplicate"):
        return None
    reason: SupersedeReason = "amendment" if line.superseded_reason == "amendment" else "duplicate"
    return Supersession(line.superseded_by, line.superseded_line, reason)
