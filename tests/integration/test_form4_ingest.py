"""E8-S2: a Form 4 task keeps the filing's XML raw first, then stores the filing, its reporting
owners and its trades; a re-run reads the raw store. The watcher routes a new Form 4 on the
issuer's listing to `form4_parse` now that its handler is registered (§4, D-063).

The SEC is a mock serving the committed filings (fixtures/sec/filings/)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, LoadMaster, connect_to, filing_routes, sec_serving

from marketdata.config import Settings
from marketdata.db.repositories import filings, insiders, raw_objects
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.form4 import Form4Ingest, Form4Refused, Form4Report
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.filings import FORM4_PARSE, FilingRow
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import build_registry, parse_insider_filing
from marketdata.tasks.registry import TaskContext, TaskRefused

Conn = psycopg.AsyncConnection[Any]

AAPL, TMQ = 320193, 1543418
APPLE_FORM4 = "0001140361-26-040127"  # Ben Borders, 2026-10-15/16: M, F, five S, four RSU lines
SOUTH32 = "0001193125-26-378380"  # South32 and its subsidiary sell Trilogy Metals shares


async def _list(
    conn: Conn, cik: int, accession: str, filed_at: date, primary_doc: str, form: str = "4"
) -> None:
    row = FilingRow(accession, cik, form, filed_at, None, None, None, primary_doc, None, None)
    await filings.insert_new(conn, [row])


async def _ingest(
    conn: Conn, tmp_path: Path, cik: int, accession: str, routes: dict[str, bytes | int]
) -> Form4Report:
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await Form4Ingest(fetcher, connect_to(conn)).run(cik, accession)


async def test_a_form_4_is_stored_raw_first_with_its_owner_and_trades(
    conn: Conn, tmp_path: Path
) -> None:
    await _list(conn, AAPL, APPLE_FORM4, date(2026, 10, 19), "xslF345X06/form4.xml")

    report = await _ingest(conn, tmp_path, AAPL, APPLE_FORM4, filing_routes())

    assert (report.form, report.owners, report.lines, report.fetched) == ("4", 1, 11, True)
    assert report.raw_key == f"raw/sec/{AAPL}/{APPLE_FORM4}/form4.xml"
    assert await raw_objects.get(conn, report.raw_key) is not None
    filing = await insiders.get_filing(conn, APPLE_FORM4)
    assert filing is not None
    assert (filing.issuer_cik, filing.form, filing.filed_at, filing.period_of_report) == (
        AAPL,
        "4",
        date(2026, 10, 19),
        date(2026, 10, 15),
    )
    assert (filing.owner_ciks, filing.rule_10b5_1, filing.doc_name, filing.raw_key) == (
        [2100523],
        True,
        "form4.xml",
        report.raw_key,
    )
    assert filing.footnotes["F4"].startswith("This transaction was made pursuant to a Rule 10b5-1")
    assert (filing.method, filing.extractor_version) == ("form4_xml", "1")
    [owner] = await insiders.owners(conn, [APPLE_FORM4])
    assert (owner.owner_name, owner.is_officer, owner.officer_title) == (
        "Borders Ben",
        True,
        "Principal Accounting Officer",
    )
    lines = await insiders.lines(conn, [APPLE_FORM4])
    assert [(line.line_no, line.is_derivative, line.code) for line in lines][:3] == [
        (1, False, "M"),
        (2, False, "F"),
        (3, False, "S"),
    ]
    sale = lines[2]
    assert (sale.trade_date, sale.shares, sale.price, sale.owned_after) == (
        date(2026, 10, 16),
        Decimal("151"),
        Decimal("343.08"),
        Decimal("39417"),
    )
    assert sale.footnotes == {"price": ["F5"], "security_title": ["F4"]}
    assert (sale.method, sale.extractor_version, sale.superseded_by) == ("form4_xml", "1", None)
    rsu = lines[10]
    assert (rsu.is_derivative, rsu.security_title, rsu.underlying_shares) == (
        True,
        "Restricted Stock Unit",
        Decimal("330"),
    )


async def test_a_rerun_reads_the_raw_store_and_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    await _list(conn, AAPL, APPLE_FORM4, date(2026, 10, 19), "xslF345X06/form4.xml")
    await _ingest(conn, tmp_path, AAPL, APPLE_FORM4, filing_routes())
    before = await insiders.lines(conn, [APPLE_FORM4])

    again = await _ingest(conn, tmp_path, AAPL, APPLE_FORM4, {})  # no SEC file served

    assert (again.fetched, again.lines) == (False, 11)
    assert await insiders.lines(conn, [APPLE_FORM4]) == before


async def test_the_xml_is_found_through_the_folder_index_when_not_at_its_name(
    conn: Conn, tmp_path: Path
) -> None:
    await _list(conn, AAPL, APPLE_FORM4, date(2026, 10, 19), "xslF345X06/renamed.xml")
    index = {"directory": {"item": [{"name": f"{APPLE_FORM4}.txt"}, {"name": "form4.xml"}]}}
    routes = {
        **filing_routes(),
        urls.filing_index_json(AAPL, APPLE_FORM4): json.dumps(index).encode(),
    }

    report = await _ingest(conn, tmp_path, AAPL, APPLE_FORM4, routes)

    assert (report.lines, report.raw_key) == (11, f"raw/sec/{AAPL}/{APPLE_FORM4}/form4.xml")


async def test_joint_filers_are_each_stored_once_with_the_filings_lines(
    conn: Conn, tmp_path: Path
) -> None:
    await _list(conn, TMQ, SOUTH32, date(2026, 9, 1), "xslF345X06/ownership.xml")

    report = await _ingest(conn, tmp_path, TMQ, SOUTH32, filing_routes())

    assert (report.owners, report.lines) == (2, 1)
    owners = await insiders.owners(conn, [SOUTH32])
    assert [(owner.position, owner.owner_cik) for owner in owners] == [(1, 1635752), (2, 1726854)]
    filing = await insiders.get_filing(conn, SOUTH32)
    assert filing is not None and filing.owner_ciks == [1635752, 1726854]


async def test_a_document_about_another_issuer_is_refused(conn: Conn, tmp_path: Path) -> None:
    """A listing with no file number whose XML names another issuer isn't this company's."""
    await _list(conn, TMQ, APPLE_FORM4, date(2026, 10, 19), "xslF345X06/form4.xml")
    apple = filing_routes()[urls.filing_document(AAPL, APPLE_FORM4, "form4.xml")]
    routes: dict[str, bytes | int] = {urls.filing_document(TMQ, APPLE_FORM4, "form4.xml"): apple}

    with pytest.raises(Form4Refused, match="about CIK 320193, not the listing's company 1543418"):
        await _ingest(conn, tmp_path, TMQ, APPLE_FORM4, routes)

    assert await insiders.get_filing(conn, APPLE_FORM4) is None


async def test_a_filing_not_listed_under_the_company_is_refused(conn: Conn, tmp_path: Path) -> None:
    with pytest.raises(Form4Refused, match="is not listed under CIK 320193"):
        await _ingest(conn, tmp_path, AAPL, APPLE_FORM4, filing_routes())


async def test_the_task_refuses_what_retrying_cant_fix(conn: Conn, tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql://unused@localhost/unused_test"
    )
    task = await queue.enqueue(
        conn, FORM4_PARSE, APPLE_FORM4, {"cik": AAPL, "accession_no": APPLE_FORM4}
    )
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        context = TaskContext(settings, connect_to(conn), fetcher, build_registry().kinds)
        with pytest.raises(TaskRefused, match="is not listed under CIK 320193"):
            await parse_insider_filing(context, task)


async def test_the_watcher_routes_a_new_form_4_to_its_task(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    """Apple's newest filing in the fixture is a Form 4 on its own listing (no file number)."""
    await load_master()
    full = (SEC_FIXTURES / "submissions" / "AAPL-20261025.json").read_bytes()
    document = json.loads(full)
    recent = document["filings"]["recent"]
    newest = recent["accessionNumber"][0]
    assert (recent["form"][0], recent["fileNumber"][0]) == ("4", "")
    for values in recent.values():
        del values[0]
    kinds = build_registry().kinds
    assert FORM4_PARSE in kinds

    for body in (json.dumps(document).encode(), full):
        async with sec_serving({urls.submissions(AAPL): body}) as sec:
            fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
            report = await FilingsWatcher(fetcher, connect_to(conn), kinds).watch(AAPL)

    assert report.stored is not None and report.stored.routed == (f"{FORM4_PARSE} {newest}",)
    [task] = await queue.list_tasks(conn, kind=FORM4_PARSE)
    assert (task.key, task.payload, task.priority) == (
        newest,
        {"cik": AAPL, "accession_no": newest, "live": True},
        queue.PRIORITY_LIVE,
    )
