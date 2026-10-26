"""E3-S4: the first watch stores a company's recent filings and routes nothing, a second watch
stores none, and one new filing enqueues exactly its task (§4)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from conftest import SEC_FIXTURES, LoadMaster, connect_to, sec_serving

from marketdata.db.repositories import filings, watch_state
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.filings import EARNINGS_EXTRACT
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue

Conn = psycopg.AsyncConnection[Any]
AAPL = 320193
EARNINGS = "0000320193-26-000018"
CORRECT = (SEC_FIXTURES / "submissions" / "AAPL-20261025.json").read_bytes()


def _without(data: bytes, accession: str) -> bytes:
    document = json.loads(data)
    recent = document["filings"]["recent"]
    index = recent["accessionNumber"].index(accession)
    for values in recent.values():
        del values[index]
    return json.dumps(document).encode()


async def _watch(conn: Conn, tmp_path: Path, body: bytes, kinds: frozenset[str]) -> Any:
    async with sec_serving({urls.submissions(AAPL): body}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await FilingsWatcher(fetcher, connect_to(conn), kinds).watch(AAPL)


async def test_first_watch_stores_recent_filings_and_a_second_stores_none(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    kinds = frozenset({EARNINGS_EXTRACT})

    first = await _watch(conn, tmp_path, CORRECT, kinds)
    same = await _watch(conn, tmp_path, CORRECT, kinds)
    reserialized = await _watch(conn, tmp_path, json.dumps(json.loads(CORRECT)).encode(), kinds)

    assert first.first_watch and first.stored.inserted == 1002 and first.stored.routed == ()
    assert same.stored is None  # identical bytes: neither stored nor parsed
    assert reserialized.stored.inserted == 0 and reserialized.stored.routed == ()
    assert await filings.count_for_company(conn, AAPL) == 1002
    assert await queue.list_tasks(conn, kind=EARNINGS_EXTRACT) == []
    state = await watch_state.get(conn, AAPL)
    assert state is not None and state.checked_at >= state.changed_at


async def test_one_new_filing_enqueues_exactly_its_task(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    kinds = frozenset({EARNINGS_EXTRACT})
    await _watch(conn, tmp_path, _without(CORRECT, EARNINGS), kinds)

    report = await _watch(conn, tmp_path, CORRECT, kinds)

    assert report.stored.inserted == 1
    assert report.stored.routed == (f"{EARNINGS_EXTRACT} {EARNINGS}",)
    [task] = await queue.list_tasks(conn, kind=EARNINGS_EXTRACT)
    assert (task.key, task.payload) == (EARNINGS, {"cik": AAPL, "accession_no": EARNINGS})
    assert task.priority == queue.PRIORITY_LIVE  # ahead of backfills (D-067)


async def test_a_shifted_file_stores_no_times_and_a_right_one_fills_them(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    shifted = (SEC_FIXTURES / "submissions" / "AAPL.json").read_bytes()

    first = await _watch(conn, tmp_path, shifted, frozenset())
    later = await _watch(conn, tmp_path, CORRECT, frozenset())

    assert not first.stored.acceptance.trusted
    assert later.stored.filled == 1002
    stored = await filings.acceptance(conn, AAPL, [EARNINGS])
    assert stored[EARNINGS] == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)  # the index page
