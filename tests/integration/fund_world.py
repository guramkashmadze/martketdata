"""E9: 13F reports for tests, stored through the real ingest (F13Ingest) from synthetic covers and
information tables, each served under the index page of Berkshire Hathaway's Q2 2026 report
(which lists `primary_doc.xml` and the table `56757.xml`)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from conftest import SEC_FIXTURES, connect_to, sec_serving

from marketdata.db.repositories import filings, managers
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.f13 import F13Ingest, F13Report
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.filings import FilingRow
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore

Conn = psycopg.AsyncConnection[Any]
INDEX = (
    SEC_FIXTURES / "filings/1067983/0001193125-26-352200/0001193125-26-352200-index.htm"
).read_bytes()
APPLE, NETFLIX, TOAST = "037833100", "64110L106", "888787108"
Q1, Q2 = date(2026, 3, 31), date(2026, 6, 30)


@dataclass(frozen=True)
class Line:
    cusip: str
    shares: int
    value: int
    name: str = "ISSUER"
    put_call: str | None = None
    share_type: str = "SH"


def cover(cik: int, period: date, lines: list[Line], form: str, kind: str | None) -> bytes:
    amendment = (
        f"<isAmendment>true</isAmendment><amendmentNo>1</amendmentNo>"
        f"<amendmentInfo><amendmentType>{kind}</amendmentType></amendmentInfo>"
        if kind
        else "<isAmendment>false</isAmendment>"
    )
    day = period.strftime("%m-%d-%Y")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler">
  <schemaVersion>X0202</schemaVersion>
  <headerData><submissionType>{form}</submissionType>
    <filerInfo><filer><credentials><cik>{cik:010d}</cik></credentials></filer>
      <periodOfReport>{day}</periodOfReport></filerInfo></headerData>
  <formData>
    <coverPage><reportCalendarOrQuarter>{day}</reportCalendarOrQuarter>{amendment}
      <filingManager><name>Manager {cik}</name></filingManager>
      <reportType>13F HOLDINGS REPORT</reportType></coverPage>
    <summaryPage><otherIncludedManagersCount>0</otherIncludedManagersCount>
      <tableEntryTotal>{len(lines)}</tableEntryTotal>
      <tableValueTotal>{sum(line.value for line in lines)}</tableValueTotal></summaryPage>
  </formData>
</edgarSubmission>""".encode()


def table(lines: list[Line]) -> bytes:
    rows = "".join(
        f"<infoTable><nameOfIssuer>{line.name}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        f"<cusip>{line.cusip}</cusip><value>{line.value}</value>"
        f"<shrsOrPrnAmt><sshPrnamt>{line.shares}</sshPrnamt>"
        f"<sshPrnamtType>{line.share_type}</sshPrnamtType></shrsOrPrnAmt>"
        + (f"<putCall>{line.put_call}</putCall>" if line.put_call else "")
        + "<investmentDiscretion>SOLE</investmentDiscretion></infoTable>"
        for line in lines
    )
    return (
        '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">'
        f"{rows}</informationTable>"
    ).encode()


async def curate(conn: Conn, ciks: dict[int, str]) -> None:
    curated = [CuratedManager(cik, name.upper(), name, "test") for cik, name in ciks.items()]
    await managers.replace_curated(conn, list(ciks), curated)


async def report(
    conn: Conn,
    tmp_path: Path,
    cik: int,
    accession: str,
    filed: date,
    period: date,
    lines: list[Line],
    *,
    form: str = "13F-HR",
    kind: str | None = None,
) -> F13Report:
    """Store a synthetic report of a curated manager through the ingest."""
    row = FilingRow(accession, cik, form, filed, None, period, None, None, None, "028-00001")
    await filings.insert_new(conn, [row])
    routes: dict[str, bytes | int] = {
        urls.filing_index_page(cik, accession): INDEX,
        urls.filing_document(cik, accession, "primary_doc.xml"): cover(
            cik, period, lines, form, kind
        ),
        urls.filing_document(cik, accession, "56757.xml"): table(lines),
    }
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await F13Ingest(fetcher, connect_to(conn)).run(cik, accession)


async def link(conn: Conn, cusip: str, ticker: str, cik: int) -> None:
    """Map a CUSIP to a security of ours, as the cusip_map task would."""
    found = await conn.execute(
        "select security_id from securities where ticker = %s and valid_to is null", [ticker]
    )
    row = await found.fetchone()
    if row is None:
        await conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) values "
            "(%s, %s, 'domestic', true) on conflict do nothing",
            [cik, f"{ticker} Inc."],
        )
        inserted = await conn.execute(
            "insert into securities (cik, ticker, security_type, exchange, valid_from) "
            "values (%s, %s, 'common', 'NASDAQ', '2020-01-01') returning security_id",
            [cik, ticker],
        )
        row = await inserted.fetchone()
    assert row is not None
    await conn.execute(
        "insert into cusip_map (cusip, status, security_id, figi, ticker) "
        "values (%s, 'matched', %s, %s, %s)",
        [cusip, row[0], f"BBG{cusip}", ticker],
    )


def price(value: int, shares: int) -> Decimal:
    return Decimal(value) / Decimal(shares)
