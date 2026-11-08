"""The stake tables (PLAN.md §7, §9.15, E16): structured Schedule 13D and 13G filings as parsed,
and their reporting persons.

A filing is stored as filed and rewritten only by a new parser version; which filing a holder
is served from is decided when read (normalize/stakes.py), over the company's stored filings.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.schedule13 import Person
from marketdata.normalize.stakes import StakeFiling, StakeRecord


class StoredStakeFiling(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    issuer_cik: int
    form: str
    filed_at: date
    filer_cik: int
    previous_accession: str | None
    amendment_no: int | None
    issuer_name: str | None
    cusips: list[str]
    class_title: str | None
    event_date: date
    rules: list[str]
    ceased_5pct: bool | None
    purpose: str | None
    checks: list[str]
    quality_flags: list[str]
    doc_name: str
    raw_key: str
    schema_version: str | None
    method: str
    extractor_version: str
    parsed_at: datetime


class StoredStakePerson(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    position: int
    name: str
    cik: int | None
    types: list[str]
    citizenship: str | None
    amount: Decimal
    percent: Decimal
    sole_voting: Decimal | None
    shared_voting: Decimal | None
    sole_dispositive: Decimal | None
    shared_dispositive: Decimal | None
    member_of_group: str | None
    method: str
    extractor_version: str


_FILING_COLUMNS = """
    accession_no, issuer_cik, form, filed_at, filer_cik, previous_accession, amendment_no,
    issuer_name, cusips, class_title, event_date, rules, ceased_5pct, purpose, checks,
    quality_flags, doc_name, raw_key, schema_version, method, extractor_version, parsed_at
"""
_PERSON_COLUMNS = """
    accession_no, position, name, cik, types, citizenship, amount, percent, sole_voting,
    shared_voting, sole_dispositive, shared_dispositive, member_of_group, method,
    extractor_version
"""

_UPSERT_FILING = """
    insert into stake_filings (
      accession_no, issuer_cik, form, filed_at, filer_cik, previous_accession, amendment_no,
      issuer_name, cusips, class_title, event_date, rules, ceased_5pct, purpose, checks,
      quality_flags, doc_name, raw_key, schema_version, method, extractor_version
    ) values (
      %(accession_no)s, %(issuer_cik)s, %(form)s, %(filed_at)s, %(filer_cik)s,
      %(previous_accession)s, %(amendment_no)s, %(issuer_name)s, %(cusips)s, %(class_title)s,
      %(event_date)s, %(rules)s, %(ceased_5pct)s, %(purpose)s, %(checks)s, %(quality_flags)s,
      %(doc_name)s, %(raw_key)s, %(schema_version)s, %(method)s, %(extractor_version)s
    )
    on conflict (accession_no) do update set
      issuer_cik = excluded.issuer_cik, form = excluded.form, filed_at = excluded.filed_at,
      filer_cik = excluded.filer_cik, previous_accession = excluded.previous_accession,
      amendment_no = excluded.amendment_no, issuer_name = excluded.issuer_name,
      cusips = excluded.cusips, class_title = excluded.class_title,
      event_date = excluded.event_date, rules = excluded.rules,
      ceased_5pct = excluded.ceased_5pct, purpose = excluded.purpose, checks = excluded.checks,
      quality_flags = excluded.quality_flags, doc_name = excluded.doc_name,
      raw_key = excluded.raw_key, schema_version = excluded.schema_version,
      method = excluded.method, extractor_version = excluded.extractor_version,
      parsed_at = now()
"""
_UPSERT_PERSON = """
    insert into stake_persons (
      accession_no, position, name, cik, types, citizenship, amount, percent, sole_voting,
      shared_voting, sole_dispositive, shared_dispositive, member_of_group, method,
      extractor_version
    ) values (
      %(accession_no)s, %(position)s, %(name)s, %(cik)s, %(types)s, %(citizenship)s,
      %(amount)s, %(percent)s, %(sole_voting)s, %(shared_voting)s, %(sole_dispositive)s,
      %(shared_dispositive)s, %(member_of_group)s, %(method)s, %(extractor_version)s
    )
    on conflict (accession_no, position) do update set
      name = excluded.name, cik = excluded.cik, types = excluded.types,
      citizenship = excluded.citizenship, amount = excluded.amount, percent = excluded.percent,
      sole_voting = excluded.sole_voting, shared_voting = excluded.shared_voting,
      sole_dispositive = excluded.sole_dispositive,
      shared_dispositive = excluded.shared_dispositive,
      member_of_group = excluded.member_of_group, method = excluded.method,
      extractor_version = excluded.extractor_version
"""
_DELETE_PERSONS_AFTER = """
    delete from stake_persons where accession_no = %(accession_no)s and position > %(last)s
"""
# E16-S3: one company's filings are decided together, so two runs about it take turns.
_LOCK = "select pg_advisory_xact_lock(hashtextextended('marketdata.stakes:' || %(cik)s, 0))"
_ABOUT = f"""
    select {_FILING_COLUMNS} from stake_filings where issuer_cik = any(%(ciks)s)
    order by filed_at, accession_no
"""
_FILINGS = f"""
    select {_FILING_COLUMNS} from stake_filings where accession_no = any(%(accessions)s)
    order by filed_at, accession_no
"""
_PERSONS = f"""
    select {_PERSON_COLUMNS} from stake_persons where accession_no = any(%(accessions)s)
    order by accession_no, position
"""
_COUNTS = """
    select form, count(*), count(distinct issuer_cik) from stake_filings group by form order by form
"""


async def store(
    conn: AsyncConnection[Any], record: StakeRecord, *, method: str, version: str
) -> None:
    """Store a parsed filing and its reporting persons as filed; a person a parser version no
    longer finds is deleted."""
    filing = record.filing
    async with conn.cursor() as cur:
        await cur.execute(
            _UPSERT_FILING,
            {
                "accession_no": record.accession_no,
                "issuer_cik": filing.issuer_cik,
                "form": filing.form,
                "filed_at": record.filed_at,
                "filer_cik": filing.filer_cik,
                "previous_accession": filing.previous_accession,
                "amendment_no": filing.amendment_no,
                "issuer_name": filing.issuer_name,
                "cusips": list(filing.cusips),
                "class_title": filing.class_title,
                "event_date": filing.event_date,
                "rules": list(filing.rules),
                "ceased_5pct": filing.ceased_5pct,
                "purpose": filing.purpose,
                "checks": list(record.checks),
                "quality_flags": list(record.flags),
                "doc_name": record.doc_name,
                "raw_key": record.raw_key,
                "schema_version": filing.schema_version,
                "method": method,
                "extractor_version": version,
            },
        )
        for person in filing.persons:
            await cur.execute(_UPSERT_PERSON, _person(record.accession_no, person, method, version))
        await cur.execute(
            _DELETE_PERSONS_AFTER,
            {"accession_no": record.accession_no, "last": len(filing.persons)},
        )


async def lock(conn: AsyncConnection[Any], ciks: Sequence[int]) -> None:
    """Hold the companies' stake lock until the transaction ends (sorted, so two runs about the
    same companies never wait on each other in turn)."""
    async with conn.cursor() as cur:
        for cik in sorted(set(ciks)):
            await cur.execute(_LOCK, {"cik": cik})


async def filings_about(conn: AsyncConnection[Any], ciks: Sequence[int]) -> list[StoredStakeFiling]:
    """Every stored filing about these companies, oldest first."""
    async with conn.cursor(row_factory=class_row(StoredStakeFiling)) as cur:
        await cur.execute(_ABOUT, {"ciks": list(ciks)})
        return await cur.fetchall()


async def filings(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredStakeFiling]:
    """These filings, oldest first."""
    async with conn.cursor(row_factory=class_row(StoredStakeFiling)) as cur:
        await cur.execute(_FILINGS, {"accessions": list(accessions)})
        return await cur.fetchall()


async def get_filing(conn: AsyncConnection[Any], accession_no: str) -> StoredStakeFiling | None:
    found = await filings(conn, [accession_no])
    return found[0] if found else None


async def persons(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredStakePerson]:
    """The reporting persons of these filings, in each filing's order."""
    async with conn.cursor(row_factory=class_row(StoredStakePerson)) as cur:
        await cur.execute(_PERSONS, {"accessions": list(accessions)})
        return await cur.fetchall()


async def counts(conn: AsyncConnection[Any]) -> list[tuple[str, int, int]]:
    """Stored filings per form: the form, the filings and the companies they are about."""
    async with conn.cursor() as cur:
        await cur.execute(_COUNTS)
        return [(str(row[0]), int(row[1]), int(row[2])) for row in await cur.fetchall()]


async def company_stakes(conn: AsyncConnection[Any], ciks: Sequence[int]) -> list[StakeFiling]:
    """Every stored filing about these companies with its persons, as the rules read them."""
    stored = await filings_about(conn, ciks)
    return as_stakes(stored, await persons(conn, [filing.accession_no for filing in stored]))


def as_stakes(
    stored: Sequence[StoredStakeFiling], people: Sequence[StoredStakePerson]
) -> list[StakeFiling]:
    by_filing: dict[str, list[StoredStakePerson]] = {}
    for person in people:
        by_filing.setdefault(person.accession_no, []).append(person)
    return [
        StakeFiling(
            accession_no=filing.accession_no,
            listing_cik=filing.issuer_cik,
            form=filing.form,
            filed_at=filing.filed_at,
            filer_cik=filing.filer_cik,
            previous_accession=filing.previous_accession,
            cusips=tuple(filing.cusips),
            ceased_5pct=filing.ceased_5pct,
            persons=tuple(by_filing.get(filing.accession_no, ())),
            extractor_version=filing.extractor_version,
        )
        for filing in stored
    ]


def _person(accession_no: str, person: Person, method: str, version: str) -> dict[str, Any]:
    return {
        "accession_no": accession_no,
        "position": person.position,
        "name": person.name,
        "cik": person.cik,
        "types": list(person.types),
        "citizenship": person.citizenship,
        "amount": person.amount,
        "percent": person.percent,
        "sole_voting": person.sole_voting,
        "shared_voting": person.shared_voting,
        "sole_dispositive": person.sole_dispositive,
        "shared_dispositive": person.shared_dispositive,
        "member_of_group": person.member_of_group,
        "method": method,
        "extractor_version": version,
    }
