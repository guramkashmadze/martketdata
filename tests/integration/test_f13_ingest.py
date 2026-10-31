"""E9-S2: a curated manager's 13F report is stored from its documents, raw first: the cover and
the information table found through the index page, values in dollars by the filing date or an
override, and the summary page's line count and total checked (PLAN.md §9.8)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import ROOT, connect_to, filing_routes, sec_serving

from marketdata.config import Settings
from marketdata.db.repositories import filings, holdings, managers
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.f13 import F13Ingest, F13Refused, F13Report, read_unit_overrides
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.filings import F13_PARSE, FilingRow
from marketdata.normalize.holdings import UnitOverride
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import build_registry, parse_13f_filing
from marketdata.tasks.registry import TaskContext, TaskRefused

Conn = psycopg.AsyncConnection[Any]
BERKSHIRE, HIMALAYA, BAUPOST = 1067983, 1709323, 1061768
Q2_2026 = "0001193125-26-352200"  # Berkshire's report for 2026-06-30, filed 2026-08-14
Q3_2022 = "0000950123-22-012275"  # Berkshire's report for 2022-09-30, filed 2022-11-14
RESTATED = "0002043585-26-000012"  # Himalaya's RESTATEMENT of its 2025-12-31 report
BAUPOST_Q2 = "0001061768-26-000010"  # filed 2026-08-13, in thousands
LISTINGS = [
    (Q2_2026, BERKSHIRE, "13F-HR", date(2026, 8, 14), date(2026, 6, 30)),
    (Q3_2022, BERKSHIRE, "13F-HR", date(2022, 11, 14), date(2022, 9, 30)),
    (RESTATED, HIMALAYA, "13F-HR/A", date(2026, 2, 19), date(2025, 12, 31)),
    (BAUPOST_Q2, BAUPOST, "13F-HR", date(2026, 8, 13), date(2026, 6, 30)),
    ("0001193125-26-344495", BERKSHIRE, "8-K", date(2026, 8, 3), None),
]


async def _seed(conn: Conn) -> None:
    curated = [
        CuratedManager(BERKSHIRE, "BERKSHIRE HATHAWAY INC", "Berkshire Hathaway", "test"),
        CuratedManager(HIMALAYA, "Himalaya Capital Management LLC", "Himalaya", "test"),
        CuratedManager(BAUPOST, "BAUPOST GROUP LLC/MA", "Baupost Group", "test"),
    ]
    await managers.replace_curated(conn, [manager.manager_cik for manager in curated], curated)
    rows = [
        FilingRow(accession, cik, form, filed, None, period, None, None, None, "028-00001")
        for accession, cik, form, filed, period in LISTINGS
    ]
    await filings.insert_new(conn, rows)


async def _run(
    conn: Conn,
    tmp_path: Path,
    cik: int,
    accession: str,
    overrides: tuple[UnitOverride, ...] = (),
) -> F13Report:
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await F13Ingest(fetcher, connect_to(conn), overrides).run(cik, accession)


async def test_a_report_is_stored_with_its_lines_and_the_summary_page_checked(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)

    report = await _run(conn, tmp_path, BERKSHIRE, Q2_2026)

    assert (report.form, report.report_period, report.lines, report.unit.unit) == (
        "13F-HR",
        "2026-06-30",
        89,
        "dollars",
    )
    assert (report.entries_match, report.totals_match, report.fetched) == (True, True, 3)
    stored = await holdings.get_filing(conn, Q2_2026)
    assert stored is not None
    assert (stored.manager_cik, stored.report_period, stored.cover_doc, stored.table_doc) == (
        BERKSHIRE,
        date(2026, 6, 30),
        "primary_doc.xml",
        "56757.xml",
    )
    assert (stored.entry_total, stored.lines, stored.value_total, stored.value_sum) == (
        89,
        89,
        Decimal("299253556246"),
        Decimal("299253556246"),
    )
    assert stored.table_raw_key == f"raw/sec/{BERKSHIRE}/{Q2_2026}/56757.xml"
    lines = await holdings.lines(conn, [Q2_2026])
    assert len(lines) == 89
    first = lines[0]
    assert (first.cusip, first.value_as_filed, first.value_usd, first.extractor_version) == (
        "02005N100",
        Decimal("577211815"),
        Decimal("577211815"),
        "1",
    )


async def test_a_second_run_reads_the_raw_store_and_changes_nothing(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)
    await _run(conn, tmp_path, BERKSHIRE, Q2_2026)
    before = await holdings.lines(conn, [Q2_2026])

    again = await _run(conn, tmp_path, BERKSHIRE, Q2_2026)

    assert again.fetched == 0  # D-070: a stored filing document is never fetched again
    assert await holdings.lines(conn, [Q2_2026]) == before


async def test_values_before_2023_are_thousands(conn: Conn, tmp_path: Path) -> None:
    await _seed(conn)

    report = await _run(conn, tmp_path, BERKSHIRE, Q3_2022)

    assert (report.unit.unit, report.lines, report.totals_match) == ("thousands", 179, True)
    first = (await holdings.lines(conn, [Q3_2022]))[0]
    assert (first.value_as_filed, first.value_usd) == (Decimal("1906458"), Decimal("1906458000"))


async def test_an_override_reads_a_filer_in_thousands_after_the_switch(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)
    overrides = read_unit_overrides(ROOT / "data" / "f13_overrides.toml")

    report = await _run(conn, tmp_path, BAUPOST, BAUPOST_Q2, overrides)

    assert report.unit.unit == "thousands"
    assert report.unit.rule.startswith("override: Baupost Group kept reporting thousands")
    stored = await holdings.get_filing(conn, BAUPOST_Q2)
    assert stored is not None and stored.value_unit == "thousands"
    alphabet = (await holdings.lines(conn, [BAUPOST_Q2]))[0]
    assert (alphabet.cusip, alphabet.value_usd) == ("02079K107", Decimal("484744000"))


async def test_an_amendment_keeps_its_type_and_number(conn: Conn, tmp_path: Path) -> None:
    await _seed(conn)

    await _run(conn, tmp_path, HIMALAYA, RESTATED)

    stored = await holdings.get_filing(conn, RESTATED)
    assert stored is not None
    assert (stored.form, stored.amendment_type, stored.amendment_no) == (
        "13F-HR/A",
        "RESTATEMENT",
        1,
    )
    assert [
        filing.accession_no
        for filing in await holdings.period_filings(conn, HIMALAYA, date(2025, 12, 31))
    ] == [RESTATED]


@pytest.mark.parametrize(
    ("cik", "accession", "message"),
    [
        (HIMALAYA, Q2_2026, "is not listed under CIK 1709323"),
        (BERKSHIRE, "0001193125-26-344495", "is a 8-K, not a 13F-HR"),
    ],
)
async def test_a_listing_that_isnt_a_managers_13f_is_refused(
    conn: Conn, tmp_path: Path, cik: int, accession: str, message: str
) -> None:
    await _seed(conn)

    with pytest.raises(F13Refused, match=message):
        await _run(conn, tmp_path, cik, accession)


async def test_a_filer_that_isnt_a_manager_is_refused(conn: Conn, tmp_path: Path) -> None:
    await _seed(conn)
    await managers.replace_curated(conn, [], [])  # Berkshire stays, no longer curated
    await conn.execute("delete from managers where manager_cik = %s", [HIMALAYA])

    with pytest.raises(F13Refused, match="CIK 1709323 is not a manager"):
        await _run(conn, tmp_path, HIMALAYA, RESTATED)


async def test_the_task_reads_the_overrides_and_refuses_what_retrying_cant_fix(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql://unused@localhost/unused_test",
        f13_overrides_file=ROOT / "data" / "f13_overrides.toml",
    )
    good = await queue.enqueue(
        conn, F13_PARSE, BAUPOST_Q2, {"cik": BAUPOST, "accession_no": BAUPOST_Q2}
    )
    bad = await queue.enqueue(conn, F13_PARSE, Q2_2026, {"cik": HIMALAYA, "accession_no": Q2_2026})
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        context = TaskContext(settings, connect_to(conn), fetcher, build_registry().kinds)
        await parse_13f_filing(context, good)
        with pytest.raises(TaskRefused, match="is not listed under CIK 1709323"):
            await parse_13f_filing(context, bad)

    stored = await holdings.get_filing(conn, BAUPOST_Q2)
    assert stored is not None and stored.value_unit == "thousands"
    assert F13_PARSE in build_registry().kinds  # so the watcher routes new 13F reports (§4)
