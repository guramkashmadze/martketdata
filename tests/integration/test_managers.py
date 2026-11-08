"""E9-S1: `managers load` checks each curated manager on EDGAR before storing it with EDGAR's
name; the scheduler watches the curated managers with the universe, and a manager that isn't a
universe company has nothing but its new 13F reports routed (§4)."""

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from conftest import (
    SEC_FIXTURES,
    TEST_UNIVERSE,
    LoadMaster,
    connect_to,
    found_soon_after,
    sec_serving,
)

from marketdata.db.repositories import managers
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.managers import ManagersLoader, ManagersReport
from marketdata.ingest.watcher import FilingsWatcher, WatchReport
from marketdata.normalize.filings import EARNINGS_EXTRACT, F13_PARSE, FACTS_REFRESH, FORM4_PARSE
from marketdata.normalize.universe import parse_managers
from marketdata.normalize.webhooks import WEBHOOK_PUBLISH
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue
from marketdata.tasks.kinds import FILINGS_WATCH
from marketdata.tasks.scheduler import enqueue_watches

Conn = psycopg.AsyncConnection[Any]
MARKEL, AAPL = 1096343, 320193
MARKEL_FILE = (SEC_FIXTURES / "submissions" / "MKL-manager-20261031.json").read_bytes()
AAPL_FILE = (SEC_FIXTURES / "submissions" / "AAPL.json").read_bytes()
REPORT = "0001096343-26-000085"  # Markel's 13F-HR for 2026-09-30, filed 2026-10-30
EIGHT_K = "0001096343-26-000082"
TEN_Q = "0001096343-26-000080"
FORM4 = "0001873434-26-000010"  # about Markel, on its own listing (no file number)
MANAGERS = (
    "display_name,manager_cik,notes\n"
    "Markel Group,1096343,EDGAR: MARKEL GROUP INC.\n"
    "Apple,320193,files no 13F\n"
    "Nobody,999999999,not on EDGAR\n"
)


async def _load(
    conn: Conn, tmp_path: Path, text: str = MANAGERS, routes: dict[str, bytes | int] | None = None
) -> ManagersReport:
    if routes is None:
        routes = {urls.submissions(MARKEL): MARKEL_FILE, urls.submissions(AAPL): AAPL_FILE}
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await ManagersLoader(fetcher, connect_to(conn)).load(parse_managers(text))


async def test_a_manager_is_stored_with_edgars_name_only_once_edgar_shows_its_13f(
    conn: Conn, tmp_path: Path
) -> None:
    report = await _load(conn, tmp_path)

    markel, apple, nobody = report.checks
    assert (markel.name, markel.latest_period, markel.latest_filed, markel.error) == (
        "MARKEL GROUP INC.",
        date(2026, 9, 30),
        date(2026, 10, 30),
        None,
    )
    assert apple.error == (
        "CIK 320193 (Apple Inc.) has filed no 13F-HR in its recent filings: check the CIK on "
        "EDGAR"
    )
    assert nobody.error == "EDGAR has no CIK 999999999"
    assert report.written == 1 and report.uncurated == ()
    [stored] = await managers.list_managers(conn)
    assert (stored.manager_cik, stored.name, stored.display_name, stored.curated) == (
        MARKEL,
        "MARKEL GROUP INC.",
        "Markel Group",
        True,
    )
    assert stored.notes == "EDGAR: MARKEL GROUP INC."


async def test_a_second_load_writes_nothing_and_a_dropped_manager_stays_uncurated(
    conn: Conn, tmp_path: Path
) -> None:
    await _load(conn, tmp_path)

    again = await _load(conn, tmp_path)
    dropped = await _load(conn, tmp_path, "display_name,manager_cik,notes\nApple,320193,none\n")

    assert (again.written, again.uncurated) == (0, ())
    assert dropped.uncurated == (MARKEL,)
    assert await managers.list_managers(conn) == []
    [kept] = await managers.list_managers(conn, curated=False)
    assert (kept.manager_cik, kept.curated) == (MARKEL, False)  # its holdings keep their manager


async def test_a_manager_that_fails_its_check_keeps_what_was_stored(
    conn: Conn, tmp_path: Path
) -> None:
    await _load(conn, tmp_path)

    report = await _load(conn, tmp_path, routes={})  # EDGAR answers 404 to everything

    assert [check.error for check in report.checks][0] == "EDGAR has no CIK 1096343"
    assert (report.written, report.uncurated) == (0, ())
    assert [manager.manager_cik for manager in await managers.list_managers(conn)] == [MARKEL]


async def test_the_scheduler_watches_the_curated_managers_with_the_universe(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    await _load(conn, tmp_path)
    # A manager that is a universe company too, as Berkshire Hathaway is, is watched once.
    await managers.replace_curated(
        conn, [MARKEL, AAPL], [CuratedManager(AAPL, "Apple Inc.", "Apple", "test")]
    )

    tasks = await enqueue_watches(conn)

    keys = sorted(int(task.key) for task in tasks)
    assert keys == sorted({*TEST_UNIVERSE.values(), MARKEL})
    assert all(task.kind == FILINGS_WATCH for task in tasks)


def _changed(data: bytes, removed: set[str], items: dict[str, str]) -> bytes:
    """The submissions file without the `removed` filings, and with other 8-K items."""
    document = json.loads(data)
    recent = document["filings"]["recent"]
    for accession, value in items.items():
        recent["items"][recent["accessionNumber"].index(accession)] = value
    keep = [i for i, accession in enumerate(recent["accessionNumber"]) if accession not in removed]
    document["filings"]["recent"] = {
        column: [values[i] for i in keep] for column, values in recent.items()
    }
    return json.dumps(document).encode()


async def _watch(
    conn: Conn, tmp_path: Path, body: bytes, clock: Callable[[], datetime] | None = None
) -> WatchReport:
    kinds = frozenset({F13_PARSE, FORM4_PARSE, FACTS_REFRESH, EARNINGS_EXTRACT})
    async with sec_serving({urls.submissions(MARKEL): body}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        watcher = (
            FilingsWatcher(fetcher, connect_to(conn), kinds, clock=clock)
            if clock
            else FilingsWatcher(fetcher, connect_to(conn), kinds)
        )
        return await watcher.watch(MARKEL)


async def test_a_manager_outside_the_universe_has_only_its_13f_routed(
    conn: Conn, tmp_path: Path
) -> None:
    await _load(conn, tmp_path)  # Markel is curated, and not a universe company
    # Its 8-K becomes a results release, which a universe company would have extracted and
    # announced.
    full = _changed(MARKEL_FILE, set(), {EIGHT_K: "2.02,9.01"})
    first = await _watch(conn, tmp_path, _changed(full, {REPORT, EIGHT_K, TEN_Q, FORM4}, {}))

    report = await _watch(conn, tmp_path, full, found_soon_after(full, REPORT))

    assert first.first_watch and first.stored is not None and first.stored.routed == ()
    assert report.stored is not None and report.stored.inserted == 4
    assert report.stored.routed == (f"{F13_PARSE} {REPORT}",)
    [task] = await queue.list_tasks(conn, kind=F13_PARSE)
    assert task.payload == {"cik": MARKEL, "accession_no": REPORT, "live": True}
    assert task.priority == queue.PRIORITY_LIVE
    assert await queue.list_tasks(conn, kind=WEBHOOK_PUBLISH) == []  # no filing.event
