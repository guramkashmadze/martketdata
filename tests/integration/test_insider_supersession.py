"""E8-S3: a 4/A or a filing made twice supersedes stored lines whichever is parsed first, and a
served trade that changes is logged in value_changes, announced as `data.changed` and served by
the change feed with the trade as served now (D-027)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from conftest import connect_to, filing_routes, sec_serving

from marketdata.db.repositories import filings, insiders, value_changes
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.form4 import Form4Ingest, Form4Report
from marketdata.normalize.filings import FilingRow
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.changes import changes_feed

Conn = psycopg.AsyncConnection[Any]
CRWV, GOOGL, NFLX = 1769628, 1652044, 1065280

# CoreWeave: a trust's sale of 2025-08-26 had the wrong acquired-or-disposed code; the 4/A
# restates that one line of ten.
ORIGINAL = ("0001415889-25-023219", "4", date(2025, 8, 28), "form4-08282025_080845.xml")
AMENDMENT = ("0001415889-25-023333", "4/A", date(2025, 8, 29), "form4a-08302025_120842.xml")
# Netflix (a director's trusts): one Form 4 filed twice on 2025-06-02, then a 4/A with its
# second part.
FIRST = ("0001062993-25-010708", "4", date(2025, 6, 2), "form4.xml")
COPY = ("0001062993-25-010709", "4", date(2025, 6, 2), "form4.xml")
SECOND_PART = ("0001062993-25-011280", "4/A", date(2025, 6, 9), "form4a.xml")


async def _store(
    conn: Conn, tmp_path: Path, cik: int, filing: tuple[str, str, date, str]
) -> Form4Report:
    accession, form, filed_at, name = filing
    row = FilingRow(
        accession, cik, form, filed_at, None, None, None, f"xslF345X05/{name}", None, None
    )
    await filings.insert_new(conn, [row])
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await Form4Ingest(fetcher, connect_to(conn)).run(cik, accession)


async def _superseded(
    conn: Conn, accession: str
) -> dict[int, tuple[str | None, int | None, str | None]]:
    return {
        line.line_no: (line.superseded_by, line.superseded_line, line.superseded_reason)
        for line in await insiders.lines(conn, [accession])
        if line.superseded_by is not None
    }


async def _events(conn: Conn) -> list[tuple[str, str]]:
    cur = await conn.execute("select type, subject from webhook_events order by created_at")
    return [(str(row[0]), str(row[1])) for row in await cur.fetchall()]


async def test_a_4a_parsed_after_its_filing_supersedes_the_line_it_corrects(
    conn: Conn, tmp_path: Path
) -> None:
    await _store(conn, tmp_path, CRWV, ORIGINAL)

    report = await _store(conn, tmp_path, CRWV, AMENDMENT)

    assert await _superseded(conn, ORIGINAL[0]) == {6: (AMENDMENT[0], 1, "amendment")}
    assert report.changes == 1
    [change] = await value_changes.page(conn, [CRWV])
    assert (change.entity, change.reason) == ("insider_transaction", "amendment")
    assert change.entity_key == {
        "accession_no": ORIGINAL[0],
        "line_no": "6",
        "field": "shares",
        "served_from": AMENDMENT[0],
    }
    assert (change.old_value, change.new_value) == (Decimal("104295"), Decimal("104295"))
    assert change.detail == (
        f"4/A {AMENDMENT[0]} line 1 corrects line 6 of {ORIGINAL[0]}: acquired or disposed A to D"
    )
    assert await _events(conn) == [("data.changed", str(change.change_id))]


async def test_a_4a_parsed_first_supersedes_the_line_when_its_filing_arrives(
    conn: Conn, tmp_path: Path
) -> None:
    await _store(conn, tmp_path, CRWV, AMENDMENT)

    report = await _store(conn, tmp_path, CRWV, ORIGINAL)

    assert await _superseded(conn, ORIGINAL[0]) == {6: (AMENDMENT[0], 1, "amendment")}
    assert report.changes == 0  # the line was never served
    assert await value_changes.page(conn, [CRWV]) == []


async def test_the_change_feed_serves_the_trade_as_served_now_with_its_proof(
    conn: Conn, tmp_path: Path
) -> None:
    await _store(conn, tmp_path, CRWV, ORIGINAL)
    await _store(conn, tmp_path, CRWV, AMENDMENT)

    feed = await changes_feed(conn)

    [item] = feed.data.changes
    assert (item.entity, item.accession_no, item.line_no, item.served_from) == (
        "insider_transaction",
        ORIGINAL[0],
        6,
        AMENDMENT[0],
    )
    assert (item.field, item.unit, item.value, item.period_end) == (
        "shares",
        "shares",
        Decimal("104295"),
        date(2025, 8, 26),
    )
    assert item.proof is not None and item.proof.model_dump() == {
        "accession_no": AMENDMENT[0],
        "form": "4/A",
        "filed_at": date(2025, 8, 29),
        "filing_url": f"https://www.sec.gov/Archives/edgar/data/{CRWV}/000141588925023333/",
        "document_url": (
            f"https://www.sec.gov/Archives/edgar/data/{CRWV}/000141588925023333/"
            "form4a-08302025_120842.xml"
        ),
        "lines": (1,),
        "method": "form4_xml",
        "extractor_version": "1",
    }
    assert [source.accession_no for source in feed.sources] == [AMENDMENT[0]]


async def test_a_4a_with_no_lines_leaves_its_filing_served(conn: Conn, tmp_path: Path) -> None:
    original = ("0000950170-25-112319", "4", date(2025, 8, 29), "ownership.xml")
    power_of_attorney = ("0000950170-25-112321", "4/A", date(2025, 8, 29), "ownership.xml")
    await _store(conn, tmp_path, GOOGL, original)

    report = await _store(conn, tmp_path, GOOGL, power_of_attorney)

    assert (report.lines, report.changes) == (0, 0)
    assert await _superseded(conn, original[0]) == {}


async def test_a_filing_made_twice_is_served_once_whichever_copy_comes_first(
    conn: Conn, tmp_path: Path
) -> None:
    await _store(conn, tmp_path, NFLX, COPY)
    copy_served = await _store(conn, tmp_path, NFLX, FIRST)
    await _store(conn, tmp_path, NFLX, SECOND_PART)

    assert await _superseded(conn, COPY[0]) == {n: (FIRST[0], n, "duplicate") for n in range(1, 31)}
    assert await _superseded(conn, FIRST[0]) == {}
    assert await _superseded(conn, SECOND_PART[0]) == {}  # its 14 sales are new trades
    # The copy was served until the first one arrived: each of its trades now served once.
    assert copy_served.changes == 30
    changes = await value_changes.page(conn, [NFLX], limit=100)
    assert {(change.reason, change.new_value) for change in changes} == {("correction", None)}


async def test_a_new_parser_version_that_reads_a_line_otherwise_is_logged(
    conn: Conn, tmp_path: Path
) -> None:
    await _store(conn, tmp_path, CRWV, ORIGINAL)
    await conn.execute(
        "update insider_transactions set shares = 1, extractor_version = '0' "
        "where accession_no = %s and line_no = 2",
        [ORIGINAL[0]],
    )
    await conn.execute(
        "update insider_filings set extractor_version = '0' where accession_no = %s", [ORIGINAL[0]]
    )

    report = await _store(conn, tmp_path, CRWV, ORIGINAL)

    assert report.changes == 1
    [change] = await value_changes.page(conn, [CRWV])
    assert (change.reason, change.old_value, change.new_value) == (
        "correction",
        Decimal("1"),
        Decimal("112272"),
    )
    assert change.detail == (f"parser version 0 to 1: line 2 of {ORIGINAL[0]}: shares 1 to 112272")
