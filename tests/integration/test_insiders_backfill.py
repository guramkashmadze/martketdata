"""E8-S4: the insiders backfill brings a company's listings up to date, then queues one
form4_parse task per Form 4 or 4/A on its own listing filed in the window, behind live work
(D-067); a listing with a file number is the company's trade in another company's shares and
isn't queued (D-063). A predecessor's Forms 4 are loaded and queued too (D-068)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, connect_to, sec_serving

from marketdata.db.repositories import filings, predecessors
from marketdata.db.repositories.predecessors import CompanyPredecessor
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.form4 import InsidersBackfill, InsidersBackfillReport
from marketdata.normalize.filings import FORM4_PARSE, FilingRow
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.handlers import build_registry

Conn = psycopg.AsyncConnection[Any]

AAPL, XOM, EXXON = 320193, 2115436, 34088
SINCE = date(2026, 7, 1)
APPLE = SEC_FIXTURES / "submissions" / "AAPL-20261025.json"
XOM_8K12B = (
    "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/"
    "0001193125-26-291990-index.htm"
)


async def _company(conn: Conn, cik: int, *, in_universe: bool = True) -> None:
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (%s, 'test', 'domestic', %s)",
        [cik, in_universe],
    )


async def _backfill(
    conn: Conn, tmp_path: Path, cik: int, routes: dict[str, bytes | int], **options: Any
) -> InsidersBackfillReport:
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        backfill = InsidersBackfill(fetcher, connect_to(conn), build_registry().kinds)
        return await backfill.run(cik, SINCE, **options)


def _own_forms_4(path: Path, since: date) -> list[str]:
    recent = json.loads(path.read_bytes())["filings"]["recent"]
    rows = zip(
        recent["accessionNumber"],
        recent["form"],
        recent["filingDate"],
        recent["fileNumber"],
        strict=True,
    )
    listed = [
        (filed, accession)
        for accession, form, filed, file_number in rows
        if form in ("4", "4/A") and not file_number and date.fromisoformat(filed) >= since
    ]
    return [accession for _, accession in sorted(listed)]


async def test_the_forms_4_on_the_companys_own_listing_in_the_window_are_queued(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)
    owner_listing = FilingRow(
        "0000320193-26-099999",
        AAPL,
        "4",
        date(2026, 9, 1),
        None,
        None,
        None,
        None,
        None,
        "001-36743",
    )
    await filings.insert_new(conn, [owner_listing])  # Apple's own trade in another company

    report = await _backfill(conn, tmp_path, AAPL, {urls.submissions(AAPL): APPLE.read_bytes()})

    expected = _own_forms_4(APPLE, SINCE)
    assert len(expected) > 20
    assert report.queued == tuple(expected)
    tasks = {task.key: task for task in await queue.list_tasks(conn, kind=FORM4_PARSE)}
    assert sorted(tasks) == sorted(expected)
    first = tasks[expected[0]]
    assert (first.payload, first.priority) == (
        {"cik": AAPL, "accession_no": expected[0]},
        queue.PRIORITY_BACKFILL,
    )


async def test_a_predecessors_forms_4_are_loaded_and_queued(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, XOM)
    await _company(conn, EXXON, in_universe=False)
    await predecessors.replace_all(
        conn,
        [CompanyPredecessor(cik=XOM, predecessor_cik=EXXON, source_url=XOM_8K12B, note="test")],
    )
    predecessor = SEC_FIXTURES / "submissions" / "XOM-predecessor-34088.json"
    routes: dict[str, bytes | int] = {
        urls.submissions(XOM): (SEC_FIXTURES / "submissions" / "XOM.json").read_bytes(),
        urls.submissions(EXXON): predecessor.read_bytes(),
    }

    report = await _backfill(conn, tmp_path, XOM, routes)

    assert report.predecessors == (EXXON,)
    assert report.queued == tuple(_own_forms_4(predecessor, SINCE))
    [task, *_] = await queue.list_tasks(conn, kind=FORM4_PARSE)
    assert task.payload["cik"] == EXXON


async def test_force_queues_filings_parsed_before_again(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL)
    routes: dict[str, bytes | int] = {urls.submissions(AAPL): APPLE.read_bytes()}
    first = await _backfill(conn, tmp_path, AAPL, routes)
    await conn.execute("update tasks set status = 'done' where kind = %s", [FORM4_PARSE])

    await _backfill(conn, tmp_path, AAPL, routes)
    kept = {task.status for task in await queue.list_tasks(conn, kind=FORM4_PARSE)}
    await _backfill(conn, tmp_path, AAPL, routes, force=True)
    again = {task.status for task in await queue.list_tasks(conn, kind=FORM4_PARSE)}

    assert first.queued and (kept, again) == ({"done"}, {"queued"})


async def test_the_backfill_needs_the_form4_kind(conn: Conn, tmp_path: Path) -> None:
    async with sec_serving({}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        backfill = InsidersBackfill(fetcher, connect_to(conn), frozenset())

        with pytest.raises(RuntimeError, match="form4_parse is not a registered"):
            await backfill.run(AAPL, SINCE)
