"""13F reports and their lines as filed (E9-S2, PLAN.md §7): `f13_filings` and `holdings_raw`.

A report is stored whole each time it is parsed: its row is upserted, its lines are upserted by
number, and lines a new parser version no longer finds are deleted.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.holdings import F13Record, in_dollars


class StoredF13Filing(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    manager_cik: int
    form: str
    filed_at: date
    report_period: date
    report_type: str | None
    amendment_no: int | None
    amendment_type: str | None
    date_reported: date | None
    conf_denied_expired: bool | None
    other_managers: int | None
    entry_total: int | None
    value_total: Decimal | None
    lines: int
    value_sum: Decimal
    value_unit: str
    unit_rule: str
    cover_doc: str
    cover_raw_key: str
    table_doc: str | None
    table_raw_key: str | None
    schema_version: str | None
    method: str
    extractor_version: str
    parsed_at: datetime


class StoredHolding(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    line_no: int
    manager_cik: int
    report_period: date
    name_of_issuer: str | None
    title_of_class: str | None
    cusip: str
    figi: str | None
    shares: Decimal
    share_type: str
    value_as_filed: Decimal
    value_usd: Decimal
    put_call: str | None
    investment_discretion: str | None
    other_managers: str | None
    method: str
    extractor_version: str


_FILING_COLUMNS = """
    accession_no, manager_cik, form, filed_at, report_period, report_type, amendment_no,
    amendment_type, date_reported, conf_denied_expired, other_managers, entry_total,
    value_total, lines, value_sum, value_unit, unit_rule, cover_doc, cover_raw_key, table_doc,
    table_raw_key, schema_version, method, extractor_version, parsed_at
"""
_LINE_COLUMNS = """
    accession_no, line_no, manager_cik, report_period, name_of_issuer, title_of_class, cusip,
    figi, shares, share_type, value_as_filed, value_usd, put_call, investment_discretion,
    other_managers, method, extractor_version
"""

_UPSERT_FILING = """
    insert into f13_filings (
      accession_no, manager_cik, form, filed_at, report_period, report_type, amendment_no,
      amendment_type, date_reported, conf_denied_expired, other_managers, entry_total,
      value_total, lines, value_sum, value_unit, unit_rule, cover_doc, cover_raw_key,
      table_doc, table_raw_key, schema_version, method, extractor_version
    ) values (
      %(accession_no)s, %(manager_cik)s, %(form)s, %(filed_at)s, %(report_period)s,
      %(report_type)s, %(amendment_no)s, %(amendment_type)s, %(date_reported)s,
      %(conf_denied_expired)s, %(other_managers)s, %(entry_total)s, %(value_total)s,
      %(lines)s, %(value_sum)s, %(value_unit)s, %(unit_rule)s, %(cover_doc)s,
      %(cover_raw_key)s, %(table_doc)s, %(table_raw_key)s, %(schema_version)s, %(method)s,
      %(extractor_version)s
    )
    on conflict (accession_no) do update set
      manager_cik = excluded.manager_cik, form = excluded.form, filed_at = excluded.filed_at,
      report_period = excluded.report_period, report_type = excluded.report_type,
      amendment_no = excluded.amendment_no, amendment_type = excluded.amendment_type,
      date_reported = excluded.date_reported,
      conf_denied_expired = excluded.conf_denied_expired,
      other_managers = excluded.other_managers, entry_total = excluded.entry_total,
      value_total = excluded.value_total, lines = excluded.lines,
      value_sum = excluded.value_sum, value_unit = excluded.value_unit,
      unit_rule = excluded.unit_rule, cover_doc = excluded.cover_doc,
      cover_raw_key = excluded.cover_raw_key, table_doc = excluded.table_doc,
      table_raw_key = excluded.table_raw_key, schema_version = excluded.schema_version,
      method = excluded.method, extractor_version = excluded.extractor_version,
      parsed_at = now()
"""
_UPSERT_LINE = """
    insert into holdings_raw (
      accession_no, line_no, manager_cik, report_period, name_of_issuer, title_of_class,
      cusip, figi, shares, share_type, value_as_filed, value_usd, put_call,
      investment_discretion, other_managers, method, extractor_version
    ) values (
      %(accession_no)s, %(line_no)s, %(manager_cik)s, %(report_period)s, %(name_of_issuer)s,
      %(title_of_class)s, %(cusip)s, %(figi)s, %(shares)s, %(share_type)s,
      %(value_as_filed)s, %(value_usd)s, %(put_call)s, %(investment_discretion)s,
      %(other_managers)s, %(method)s, %(extractor_version)s
    )
    on conflict (accession_no, line_no) do update set
      manager_cik = excluded.manager_cik, report_period = excluded.report_period,
      name_of_issuer = excluded.name_of_issuer, title_of_class = excluded.title_of_class,
      cusip = excluded.cusip, figi = excluded.figi, shares = excluded.shares,
      share_type = excluded.share_type, value_as_filed = excluded.value_as_filed,
      value_usd = excluded.value_usd, put_call = excluded.put_call,
      investment_discretion = excluded.investment_discretion,
      other_managers = excluded.other_managers, method = excluded.method,
      extractor_version = excluded.extractor_version
"""
_DELETE_LINES_AFTER = """
    delete from holdings_raw where accession_no = %(accession_no)s and line_no > %(last)s
    returning line_no
"""
_GET_FILING = f"select {_FILING_COLUMNS} from f13_filings where accession_no = %(a)s"
_PERIOD_FILINGS = f"""
    select {_FILING_COLUMNS} from f13_filings
    where manager_cik = %(cik)s and report_period = %(period)s
    order by filed_at, accession_no
"""
_LINES = f"""
    select {_LINE_COLUMNS} from holdings_raw where accession_no = any(%(accessions)s)
    order by accession_no, line_no
"""


async def store(
    conn: AsyncConnection[Any], record: F13Record, *, method: str, version: str
) -> list[int]:
    """Store a parsed report and its lines as filed, values also in dollars; a line a parser
    version no longer finds is deleted. Returns the numbers of the lines deleted."""
    cover = record.cover
    async with conn.cursor() as cur:
        await cur.execute(
            _UPSERT_FILING,
            {
                "accession_no": record.accession_no,
                "manager_cik": record.manager_cik,
                "form": record.form,
                "filed_at": record.filed_at,
                "report_period": cover.report_period,
                "report_type": cover.report_type,
                "amendment_no": cover.amendment_no,
                "amendment_type": cover.amendment_type,
                "date_reported": cover.date_reported,
                "conf_denied_expired": cover.conf_denied_expired,
                "other_managers": cover.other_managers,
                "entry_total": cover.entry_total,
                "value_total": cover.value_total,
                "lines": len(record.holdings),
                "value_sum": record.value_sum,
                "value_unit": record.unit.unit,
                "unit_rule": record.unit.rule,
                "cover_doc": record.cover_doc,
                "cover_raw_key": record.cover_raw_key,
                "table_doc": record.table_doc,
                "table_raw_key": record.table_raw_key,
                "schema_version": cover.schema_version,
                "method": method,
                "extractor_version": version,
            },
        )
        rows = [
            {
                "accession_no": record.accession_no,
                "line_no": holding.line_no,
                "manager_cik": record.manager_cik,
                "report_period": cover.report_period,
                "name_of_issuer": holding.name_of_issuer,
                "title_of_class": holding.title_of_class,
                "cusip": holding.cusip,
                "figi": holding.figi,
                "shares": holding.shares,
                "share_type": holding.share_type,
                "value_as_filed": holding.value,
                "value_usd": in_dollars(holding.value, record.unit.unit),
                "put_call": holding.put_call,
                "investment_discretion": holding.investment_discretion,
                "other_managers": holding.other_managers,
                "method": method,
                "extractor_version": version,
            }
            for holding in record.holdings
        ]
        if rows:  # a report with no holdings has no table
            await cur.executemany(_UPSERT_LINE, rows)
        await cur.execute(
            _DELETE_LINES_AFTER,
            {"accession_no": record.accession_no, "last": len(record.holdings)},
        )
        return sorted(int(row[0]) for row in await cur.fetchall())


async def get_filing(conn: AsyncConnection[Any], accession_no: str) -> StoredF13Filing | None:
    async with conn.cursor(row_factory=class_row(StoredF13Filing)) as cur:
        await cur.execute(_GET_FILING, {"a": accession_no})
        return await cur.fetchone()


async def period_filings(
    conn: AsyncConnection[Any], manager_cik: int, report_period: date
) -> list[StoredF13Filing]:
    """A manager's reports for one quarter, in filing order: the report, then its amendments."""
    async with conn.cursor(row_factory=class_row(StoredF13Filing)) as cur:
        await cur.execute(_PERIOD_FILINGS, {"cik": manager_cik, "period": report_period})
        return await cur.fetchall()


async def lines(conn: AsyncConnection[Any], accessions: Sequence[str]) -> list[StoredHolding]:
    async with conn.cursor(row_factory=class_row(StoredHolding)) as cur:
        await cur.execute(_LINES, {"accessions": list(accessions)})
        return await cur.fetchall()
