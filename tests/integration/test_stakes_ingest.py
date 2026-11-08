"""E16-S2, S3, S4: a stake filing is kept raw first, then stored with its reporting persons and its
checks; a holder's later filing that changes a served stake is a value change announced as
`data.changed`, whatever order the filings arrive in; the backfill reads the listings already
stored and announces nothing.

The SEC is a mock serving the committed filings (fixtures/sec/filings/)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import connect_to, filing_routes, sec_serving
from stake_world import (
    AMD,
    FRESENIUS_AUGUST,
    FRESENIUS_JULY,
    HUMACYTE,
    NVIDIA,
    NVIDIA_NEBIUS,
    VANGUARD_AMD,
    VCM_AMD,
    WALTON,
    backfill,
    ingest,
    list_filing,
)

from marketdata.config import Settings
from marketdata.db.repositories import raw_objects, stakes, value_changes
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.stakes import StakeRefused
from marketdata.normalize.filings import STAKE_PARSE, FilingRow, route
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import build_registry, parse_stake_filing
from marketdata.tasks.kinds import STAKES_BACKFILL
from marketdata.tasks.registry import TaskContext, TaskRefused

Conn = psycopg.AsyncConnection[Any]


async def _changes(conn: Conn) -> list[Any]:
    return list(reversed(await value_changes.page(conn, limit=100)))


async def _events(conn: Conn) -> list[tuple[str, str]]:
    cur = await conn.execute("select type, subject from webhook_events order by created_at")
    return [(str(row[0]), str(row[1])) for row in await cur.fetchall()]


async def test_a_13g_is_stored_raw_first_with_its_persons_and_checks(
    conn: Conn, tmp_path: Path
) -> None:
    await list_filing(conn, VCM_AMD)

    report = await ingest(conn, tmp_path, VCM_AMD)

    assert (report.form, report.persons, report.fetched, report.changes) == (
        "SCHEDULE 13G",
        1,
        True,
        0,
    )
    assert report.raw_key == f"raw/sec/{AMD}/{VCM_AMD.accession_no}/primary_doc.xml"
    assert await raw_objects.get(conn, report.raw_key) is not None
    filing = await stakes.get_filing(conn, VCM_AMD.accession_no)
    assert filing is not None
    assert (filing.issuer_cik, filing.form, filing.filed_at, filing.filer_cik) == (
        AMD,
        "SCHEDULE 13G",
        date(2026, 4, 29),
        2100119,
    )
    assert (filing.cusips, filing.class_title, filing.event_date, filing.rules) == (
        ["007903107"],
        "Common Stock",
        date(2026, 3, 31),
        ["Rule 13d-1(b)"],
    )
    assert (filing.doc_name, filing.raw_key, filing.schema_version) == (
        "primary_doc.xml",
        report.raw_key,
        "X0202",
    )
    assert filing.checks == [
        "issuer_matches_listing",
        "form_matches_listing",
        "percent_in_range",
        "powers_within_amount",
    ]
    assert filing.quality_flags == []
    assert (filing.method, filing.extractor_version) == ("schedule13_xml", "1")
    [person] = await stakes.persons(conn, [VCM_AMD.accession_no])
    assert (person.name, person.cik, person.types, person.amount, person.percent) == (
        "Vanguard Capital Management",
        None,
        ["IA"],
        Decimal(122092206),
        Decimal("7.48"),
    )
    assert (person.method, person.extractor_version) == ("schedule13_xml", "1")


async def test_a_rerun_reads_the_raw_store_and_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    await list_filing(conn, WALTON)
    await ingest(conn, tmp_path, WALTON)
    before = await stakes.persons(conn, [WALTON.accession_no])

    again = await ingest(conn, tmp_path, WALTON, routes={})  # no SEC file served

    assert (again.fetched, again.persons, again.changes) == (False, 2, 0)
    assert await stakes.persons(conn, [WALTON.accession_no]) == before
    assert await _changes(conn) == []
    filing = await stakes.get_filing(conn, WALTON.accession_no)
    assert filing is not None and filing.purpose is not None
    assert filing.purpose.startswith("Prior to December 18, 2024, Alice L. Walton")


async def test_a_later_filing_that_changes_a_served_stake_is_announced(
    conn: Conn, tmp_path: Path
) -> None:
    """Fresenius's US holding company filed 18,312,735 shares (6.8%) in July and 13,603,235
    (4.9%, below 5%) in August 2026."""
    await list_filing(conn, FRESENIUS_JULY)
    await list_filing(conn, FRESENIUS_AUGUST)
    await ingest(conn, tmp_path, FRESENIUS_JULY)

    report = await ingest(conn, tmp_path, FRESENIUS_AUGUST)

    assert report.changes == 1
    [change] = await _changes(conn)
    assert (change.entity, change.cik, change.reason) == ("stake_holding", HUMACYTE, "amendment")
    assert change.entity_key == {
        "accession_no": FRESENIUS_JULY.accession_no,
        "served_from": FRESENIUS_AUGUST.accession_no,
    }
    assert (change.old_value, change.new_value) == (
        Decimal("18312735.00"),
        Decimal("13603235.00"),
    )
    assert change.detail == (
        "SCHEDULE 13D/A 0001104659-26-098152 filed 2026-08-18 replaces SCHEDULE 13D/A "
        "0001104659-26-083164 filed 2026-07-13: 18,312,735.00 shares (6.8% of the class) to "
        "13,603,235.00 shares (4.9% of the class, exited)"
    )
    assert await _events(conn) == [("data.changed", str(change.change_id))]


async def test_an_older_filing_stored_after_a_newer_one_changes_nothing(
    conn: Conn, tmp_path: Path
) -> None:
    await list_filing(conn, FRESENIUS_JULY)
    await list_filing(conn, FRESENIUS_AUGUST)
    await ingest(conn, tmp_path, FRESENIUS_AUGUST)

    report = await ingest(conn, tmp_path, FRESENIUS_JULY)

    assert report.changes == 0
    assert await _changes(conn) == []


async def test_two_holders_of_one_company_change_nothing_of_each_other(
    conn: Conn, tmp_path: Path
) -> None:
    """The Vanguard Group's exit and Vanguard Capital Management's new 13G on AMD are two
    filers' statements, so one doesn't replace the other."""
    await list_filing(conn, VANGUARD_AMD)
    await list_filing(conn, VCM_AMD)
    await ingest(conn, tmp_path, VANGUARD_AMD)

    report = await ingest(conn, tmp_path, VCM_AMD)

    assert report.changes == 0


async def test_a_document_about_another_company_is_refused(conn: Conn, tmp_path: Path) -> None:
    listed = await list_filing(conn, VCM_AMD, cik=HUMACYTE)
    amd = filing_routes()[urls.filing_document(AMD, VCM_AMD.accession_no, "primary_doc.xml")]
    routes: dict[str, bytes | int] = {
        urls.filing_document(HUMACYTE, VCM_AMD.accession_no, "primary_doc.xml"): amd
    }

    with pytest.raises(StakeRefused, match="about CIK 2488, not the listing's company 1818382"):
        await ingest(conn, tmp_path, listed, routes)

    assert await stakes.get_filing(conn, VCM_AMD.accession_no) is None


async def test_the_filers_own_listing_is_refused(conn: Conn, tmp_path: Path) -> None:
    """NVIDIA's own listing of its 13G on Nebius has no file number: the filing is Nebius's."""
    listed = await list_filing(conn, NVIDIA_NEBIUS, cik=NVIDIA, file_number=None)

    with pytest.raises(StakeRefused, match="is the filer's own, not the subject company's"):
        await ingest(conn, tmp_path, listed)


async def test_an_old_format_filing_is_refused(conn: Conn, tmp_path: Path) -> None:
    listed = await list_filing(conn, VCM_AMD, form="SC 13G")

    with pytest.raises(StakeRefused, match="SC 13G is not a structured 13D or 13G"):
        await ingest(conn, tmp_path, listed)


async def test_a_filing_not_listed_under_the_company_is_refused(conn: Conn, tmp_path: Path) -> None:
    with pytest.raises(StakeRefused, match="is not listed under CIK 2488"):
        await ingest(conn, tmp_path, VCM_AMD)


async def test_the_task_refuses_what_retrying_cant_fix(conn: Conn, tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql://unused@localhost/unused_test"
    )
    task = await queue.enqueue(
        conn, STAKE_PARSE, VCM_AMD.accession_no, {"cik": AMD, "accession_no": VCM_AMD.accession_no}
    )
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        context = TaskContext(settings, connect_to(conn), fetcher, build_registry().kinds)
        with pytest.raises(TaskRefused, match="is not listed under CIK 2488"):
            await parse_stake_filing(context, task)


def test_both_stake_tasks_are_registered() -> None:
    kinds = build_registry().kinds

    assert {STAKE_PARSE, STAKES_BACKFILL} <= kinds


@pytest.mark.parametrize(
    ("form", "file_number", "routed"),
    [
        ("SCHEDULE 13G", "005-07443", True),  # the subject company's listing
        ("SCHEDULE 13D/A", "005-91701", True),
        ("SCHEDULE 13G", None, False),  # the filer's own listing (D-061)
        ("SC 13G/A", "005-07443", False),  # the old format stays a link
    ],
)
def test_the_watcher_routes_a_structured_filing_on_the_subjects_listing(
    form: str, file_number: str | None, routed: bool
) -> None:
    row = FilingRow(
        "0002100119-26-000052", AMD, form, date(2026, 4, 29), None, None, None,
        "xslSCHEDULE_13G_X02/primary_doc.xml", None, file_number,
    )  # fmt: skip

    routes = route(row, filer_type="domestic")

    assert [work.kind for work in routes] == ([STAKE_PARSE] if routed else [])


def test_a_manager_watched_only_as_a_manager_routes_no_stake() -> None:
    row = FilingRow(
        "0002100119-26-000052", AMD, "SCHEDULE 13G", date(2026, 4, 29), None, None, None,
        None, None, "005-07443",
    )  # fmt: skip

    assert route(row, filer_type="other", curated_manager=True, manager_only=True) == []


async def test_the_backfill_reads_the_stored_listings_and_announces_nothing(
    conn: Conn, tmp_path: Path
) -> None:
    for listed in (FRESENIUS_JULY, FRESENIUS_AUGUST):
        await list_filing(conn, listed)
    await list_filing(conn, VCM_AMD, cik=HUMACYTE, accession_no="0000000000-26-000001")
    await list_filing(conn, VCM_AMD, cik=HUMACYTE, accession_no="0000000000-26-000002",
                      file_number=None)  # fmt: skip
    routes = filing_routes()  # no submissions file: a watch would fail

    report = await backfill(conn, tmp_path, HUMACYTE, routes=routes)

    assert (report.group, report.listed, report.kept, report.changes) == ((HUMACYTE,), 3, 0, 0)
    assert report.parsed == (FRESENIUS_JULY.accession_no, FRESENIUS_AUGUST.accession_no)
    assert report.refused == ("0000000000-26-000001",)  # no XML served for it
    assert await _changes(conn) == []
    assert await _events(conn) == []

    again = await backfill(conn, tmp_path, HUMACYTE, routes={})

    assert (again.kept, again.parsed, again.changes) == (2, (), 0)

    forced = await backfill(conn, tmp_path, HUMACYTE, force=True, routes={})

    assert forced.parsed == (FRESENIUS_JULY.accession_no, FRESENIUS_AUGUST.accession_no)
    assert forced.changes == 0  # read the same again
