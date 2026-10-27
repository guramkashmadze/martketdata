"""E4a-S1: a facts refresh stores the company's companyfacts JSON, then loads its us-gaap and dei
facts; running it again changes nothing. A predecessor's file is loaded with it (D-068), and a
new 10-K or 10-Q the SEC's file doesn't list yet makes the task retry (D-081)."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, LoadMaster, connect_to, sec_serving

from marketdata.db.repositories import filings, predecessors, raw_objects, xbrl_facts
from marketdata.db.repositories.predecessors import CompanyPredecessor
from marketdata.ingest.facts import FactsLoader, FactsNotPublished, FactsReport
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.filings import FACTS_REFRESH, FilingRow
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.scheduler import enqueue_facts_backfills

Conn = psycopg.AsyncConnection[Any]

AAPL, XOM, EXXON = 320193, 2115436, 34088
AAPL_FACTS = (SEC_FIXTURES / "companyfacts" / "AAPL.json").read_bytes()
AAPL_SUBMISSIONS = (SEC_FIXTURES / "submissions" / "AAPL-20261025.json").read_bytes()
TEN_Q = "0000320193-26-000020"  # Apple's 10-Q of 2026-07-31, in the fixture
NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _count(data: bytes) -> int:
    """Facts counted straight from the JSON: every entry in a unit list of us-gaap or dei."""
    document = json.loads(data)
    return sum(
        len(rows)
        for taxonomy in ("us-gaap", "dei")
        for concept in document["facts"].get(taxonomy, {}).values()
        for rows in concept["units"].values()
    )


def _facts(cik: int, *accessions: str) -> bytes:
    rows = [
        {
            "end": "2026-06-30",
            "val": 100 + number,
            "accn": accession,
            "fy": 2026,
            "fp": "Q2",
            "form": "10-Q",
            "filed": "2026-08-03",
        }
        for number, accession in enumerate(accessions)
    ]
    return json.dumps(
        {"cik": cik, "facts": {"us-gaap": {"Assets": {"units": {"USD": rows}}}}}
    ).encode()


async def _company(conn: Conn, cik: int, *, in_universe: bool = True) -> None:
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (%s, 'test', 'domestic', %s)",
        [cik, in_universe],
    )


def _ten_q(cik: int, accession: str, filed: date) -> FilingRow:
    return FilingRow(
        accession_no=accession,
        cik=cik,
        form="10-Q",
        filed_at=filed,
        accepted_at=None,
        period_of_report=date(2026, 6, 27),
        items_8k=None,
        primary_doc=None,
        is_xbrl=True,
        file_number=None,
    )


async def _refresh(
    conn: Conn,
    tmp_path: Path,
    cik: int,
    routes: dict[str, bytes | int],
    *,
    now: datetime = NOW,
) -> FactsReport:
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await FactsLoader(fetcher, connect_to(conn)).refresh(cik, now=now)


async def test_the_fact_count_matches_the_fixture_and_the_file_is_kept(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)

    report = await _refresh(conn, tmp_path, AAPL, {urls.companyfacts(AAPL): AAPL_FACTS})

    [loaded] = report.files
    assert await xbrl_facts.count(conn, AAPL) == loaded.facts == _count(AAPL_FACTS) == 5895
    assert loaded.written is not None and loaded.written.inserted == 5895
    assert loaded.raw_key is not None and loaded.raw_key.startswith(
        "raw/sec/api/companyfacts/320193/"
    )
    stored = await raw_objects.get(conn, loaded.raw_key)
    assert stored is not None and stored.size_bytes == len(AAPL_FACTS)
    assert (tmp_path / "raw" / loaded.raw_key).read_bytes() == AAPL_FACTS  # as served


async def test_a_second_refresh_changes_nothing_and_keeps_its_file_too(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)
    routes: dict[str, bytes | int] = {urls.companyfacts(AAPL): AAPL_FACTS}
    first = await _refresh(conn, tmp_path, AAPL, routes)

    again = await _refresh(conn, tmp_path, AAPL, routes)

    [loaded] = again.files
    assert loaded.written is not None
    assert (loaded.written.inserted, loaded.written.updated) == (0, 0)
    assert await xbrl_facts.count(conn, AAPL) == 5895
    assert loaded.raw_key != first.files[0].raw_key  # every response is stored (D-081)


async def test_a_frame_that_moved_updates_both_facts(conn: Conn, tmp_path: Path) -> None:
    # The SEC gives a calendar frame to the latest filing that reports the period.
    await _company(conn, AAPL)
    document = json.loads(AAPL_FACTS)
    rows = document["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"]
    framed = [row for row in rows if row.get("frame") == "CY2025Q2"]
    [newer] = framed
    [older] = [
        row
        for row in rows
        if (row.get("start"), row["end"]) == (newer["start"], newer["end"])
        and row["accn"] != newer["accn"]
    ]
    await _refresh(conn, tmp_path, AAPL, {urls.companyfacts(AAPL): AAPL_FACTS})
    older["frame"] = newer.pop("frame")

    report = await _refresh(
        conn, tmp_path, AAPL, {urls.companyfacts(AAPL): json.dumps(document).encode()}
    )

    written = report.files[0].written
    assert written is not None and (written.inserted, written.updated, written.moved) == (0, 2, ())
    stored = await conn.execute(
        "select accession_no, frame from xbrl_facts where cik = %s and concept = %s "
        "and period_start = %s and period_end = %s order by accession_no",
        [AAPL, "EarningsPerShareDiluted", date(2025, 3, 30), date(2025, 6, 28)],
    )
    assert await stored.fetchall() == [
        (older["accn"], "CY2025Q2"),
        (newer["accn"], None),
    ]


async def test_a_predecessors_facts_are_loaded_with_the_company(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, XOM)
    await _company(conn, EXXON, in_universe=False)
    source = "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/"
    link = CompanyPredecessor(cik=XOM, predecessor_cik=EXXON, source_url=source, note="test")
    await predecessors.replace_all(conn, [link])
    routes: dict[str, bytes | int] = {
        urls.companyfacts(XOM): _facts(XOM, "0002115436-26-000010"),
        urls.companyfacts(EXXON): _facts(EXXON, "0000034088-26-000093", "0000034088-26-000067"),
    }

    report = await _refresh(conn, tmp_path, XOM, routes)

    assert [(file.cik, file.facts) for file in report.files] == [(XOM, 1), (EXXON, 2)]
    assert (await xbrl_facts.count(conn, XOM), await xbrl_facts.count(conn, EXXON)) == (1, 2)


async def test_a_company_without_companyfacts_has_no_facts_yet(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL)

    report = await _refresh(conn, tmp_path, AAPL, {urls.companyfacts(AAPL): 404})

    assert [(file.raw_key, file.facts, file.written) for file in report.files] == [(None, 0, None)]


async def test_a_new_filing_missing_from_the_file_is_waited_for(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL)
    today = datetime.now(UTC).date()
    await filings.insert_new(conn, [_ten_q(AAPL, "0000320193-26-000030", today)])
    [listed] = await filings.list_for_company(conn, AAPL)
    routes: dict[str, bytes | int] = {urls.companyfacts(AAPL): AAPL_FACTS}

    with pytest.raises(FactsNotPublished, match="0000320193-26-000030"):
        await _refresh(
            conn, tmp_path, AAPL, routes, now=listed.discovered_at + timedelta(minutes=5)
        )
    later = await _refresh(
        conn, tmp_path, AAPL, routes, now=listed.discovered_at + timedelta(hours=2)
    )

    assert later.missing == ("0000320193-26-000030",)  # logged, no longer waited for
    assert await xbrl_facts.count(conn, AAPL) == 5895  # the facts stand either way


async def test_a_filing_the_file_holds_is_not_waited_for(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL)
    await filings.insert_new(conn, [_ten_q(AAPL, TEN_Q, datetime.now(UTC).date())])
    [listed] = await filings.list_for_company(conn, AAPL)

    report = await _refresh(
        conn,
        tmp_path,
        AAPL,
        {urls.companyfacts(AAPL): AAPL_FACTS},
        now=listed.discovered_at + timedelta(minutes=1),
    )

    assert report.missing == ()


async def test_every_new_periodic_filing_queues_the_refresh_again(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    # Keyed by company, a refresh that finished must run again for the next 10-Q (D-058).
    await load_master()
    document = json.loads(AAPL_SUBMISSIONS)
    recent = document["filings"]["recent"]
    ten_qs = [index for index, form in enumerate(recent["form"]) if form == "10-Q"][:2]
    without = json.loads(AAPL_SUBMISSIONS)
    for index in sorted(ten_qs, reverse=True):
        for values in without["filings"]["recent"].values():
            del values[index]
    one_back = json.loads(AAPL_SUBMISSIONS)
    for values in one_back["filings"]["recent"].values():
        del values[ten_qs[1]]
    kinds = frozenset({FACTS_REFRESH})

    async def watch(body: dict[str, Any]) -> None:
        async with sec_serving({urls.submissions(AAPL): json.dumps(body).encode()}) as sec:
            fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
            await FilingsWatcher(fetcher, connect_to(conn), kinds).watch(AAPL)

    await watch(without)  # the first watch routes nothing (D-063)
    await watch(one_back)  # the newest 10-Q
    claimed = await queue.claim(conn, [FACTS_REFRESH], timedelta(minutes=5))
    assert claimed is not None
    await queue.complete(conn, claimed)
    await watch(document)  # the 10-Q before it

    [task] = await queue.list_tasks(conn, kind=FACTS_REFRESH)
    assert (task.key, task.status, task.payload) == (str(AAPL), "queued", {"cik": AAPL})
    assert task.priority == queue.PRIORITY_LIVE


async def test_the_backfill_queues_domestic_universe_companies_behind_live_work(
    conn: Conn, load_master: LoadMaster
) -> None:
    await load_master()  # AAPL, GOOGL, TGT, SHOP and SPCX are domestic; the others aren't

    every = await enqueue_facts_backfills(conn)
    one = await enqueue_facts_backfills(conn, ciks={AAPL})

    assert sorted(task.key for task in every) == sorted(
        str(cik) for cik in (320193, 1652044, 27419, 1594805, 1181412)
    )
    assert {(task.kind, task.priority) for task in every} == {
        (FACTS_REFRESH, queue.PRIORITY_BACKFILL)
    }
    assert [task.key for task in one] == [str(AAPL)]
