"""E9-S3: the CUSIPs of stored 13F lines are asked about on OpenFIGI once, raw first; matches
link to our securities, unmatched and ambiguous ones are stored with why, and unmatched ones
are asked again after 30 days (D-121)."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx2
import psycopg
from conftest import LoadMaster, connect_to, filing_routes, sec_serving

from marketdata.db.repositories import cusips, filings, managers, raw_objects, securities
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.cusips import RECHECK_AFTER, CusipMapper, MappingReport
from marketdata.ingest.f13 import F13Ingest
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.cusips import CUSIP_MAP, PENDING
from marketdata.normalize.filings import FilingRow
from marketdata.openfigi.client import OpenFigiClient
from marketdata.sec.client import TokenBucket
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue

Conn = psycopg.AsyncConnection[Any]
BERKSHIRE = 1067983
REPORT = "0001193125-26-352200"  # Berkshire's Q2 2026: 89 lines, 29 CUSIPs


def _instrument(figi: str, ticker: str, name: str) -> dict[str, str]:
    return {
        "figi": figi,
        "ticker": ticker,
        "name": name,
        "exchCode": "US",
        "securityType": "Common Stock",
        "marketSector": "Equity",
    }


ANSWERS: dict[str, Any] = {
    "037833100": {"data": [_instrument("BBG000B9XRY4", "AAPL", "APPLE INC")]},
    "02079K305": {"data": [_instrument("BBG009S39JX6", "GOOGL", "ALPHABET INC-CL A")]},
    "H1467J104": {"data": [_instrument("BBG000BR14K5", "CB", "CHUBB LTD")]},
    "650111107": {
        "data": [
            _instrument("BBG000BPW8T1", "NYT", "NEW YORK TIMES CO-A"),
            _instrument("BBG000FAKE01", "NYT2", "NEW YORK TIMES CO"),
        ]
    },
}


NOT_FOUND = {"warning": "No identifier found."}


class FakeOpenFigi:
    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        jobs = json.loads(request.content)
        self.asked.append([job["idValue"] for job in jobs])
        # OpenFIGI finds an issuer outside the US only by its CINS number (Chubb's starts with H).
        return httpx2.Response(
            200,
            json=[
                (
                    ANSWERS.get(job["idValue"], NOT_FOUND)
                    if job["idType"] == ("ID_CINS" if job["idValue"][0].isalpha() else "ID_CUSIP")
                    else NOT_FOUND
                )
                for job in jobs
            ],
        )


async def _store_report(conn: Conn, tmp_path: Path, *, routed: bool = False) -> None:
    manager = CuratedManager(BERKSHIRE, "BERKSHIRE HATHAWAY INC", "Berkshire Hathaway", "test")
    await managers.replace_curated(conn, [BERKSHIRE], [manager])
    row = FilingRow(
        REPORT,
        BERKSHIRE,
        "13F-HR",
        date(2026, 8, 14),
        None,
        date(2026, 6, 30),
        None,
        None,
        None,
        "028-04545",
    )
    await filings.insert_new(conn, [row])
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await F13Ingest(fetcher, connect_to(conn)).run(BERKSHIRE, REPORT, routed=routed)


async def _map(
    conn: Conn, tmp_path: Path, fake: FakeOpenFigi, recheck_after: timedelta = RECHECK_AFTER
) -> MappingReport:
    client = OpenFigiClient(
        None, bucket=TokenBucket(1e6), transport=httpx2.MockTransport(fake.handle)
    )
    async with sec_serving({}) as sec, client:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        mapper = CusipMapper(client, fetcher, connect_to(conn), recheck_after=recheck_after)
        return await mapper.run()


async def test_each_cusip_is_asked_once_and_matches_link_to_our_securities(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await load_master()
    await _store_report(conn, tmp_path)
    fake = FakeOpenFigi()

    report = await _map(conn, tmp_path, fake)

    assert [len(batch) for batch in fake.asked] == [10, 10, 9]  # 10 jobs a request, no key
    assert (report.asked, report.requests, report.matched, report.ambiguous) == (29, 3, 3, 1)
    assert (report.unmatched, report.invalid) == (25, 0)
    stored = {
        row.cusip: row
        for row in await cusips.get_many(
            conn, ["037833100", "02079K305", "H1467J104", "650111107", "02005N100"]
        )
    }
    [apple] = await securities.find_current(conn, ["AAPL"])
    assert (stored["037833100"].status, stored["037833100"].security_id) == (
        "matched",
        apple.security_id,
    )
    assert stored["037833100"].figi == "BBG000B9XRY4"
    assert stored["02079K305"].security_id is not None  # GOOGL
    assert (stored["H1467J104"].status, stored["H1467J104"].security_id) == ("matched", None)
    assert (stored["650111107"].status, stored["650111107"].figi) == ("ambiguous", None)
    assert stored["02005N100"].note == "OpenFIGI: No identifier found."
    # Raw first: each request and its response are in the raw store.
    raw_key = stored["037833100"].raw_key
    assert raw_key is not None and raw_key.startswith("raw/openfigi/mapping/")
    assert await raw_objects.get(conn, raw_key) is not None
    assert await raw_objects.get(conn, raw_key.removesuffix(".json") + ".request.json") is not None


async def test_a_second_run_asks_nothing_until_unmatched_cusips_are_due_again(
    conn: Conn, tmp_path: Path
) -> None:
    await _store_report(conn, tmp_path)
    await _map(conn, tmp_path, FakeOpenFigi())
    again, due = FakeOpenFigi(), FakeOpenFigi()

    second = await _map(conn, tmp_path, again)
    later = await _map(conn, tmp_path, due, recheck_after=timedelta(0))

    assert (second.asked, again.asked) == (0, [])
    assert later.asked == 26  # the 25 unmatched and the ambiguous one; matches aren't asked
    assert "037833100" not in [cusip for batch in due.asked for cusip in batch]


async def test_a_match_is_linked_once_we_list_its_ticker(
    conn: Conn, tmp_path: Path, load_master: LoadMaster
) -> None:
    await _store_report(conn, tmp_path)
    await _map(conn, tmp_path, FakeOpenFigi())  # no security master loaded yet
    [before] = await cusips.get_many(conn, ["037833100"])
    await load_master()

    report = await _map(conn, tmp_path, FakeOpenFigi())

    [after] = await cusips.get_many(conn, ["037833100"])
    assert (before.security_id, report.asked, report.linked) == (None, 0, 2)  # AAPL and GOOGL
    assert after.security_id is not None and after.figi == before.figi


async def test_a_wrong_check_digit_is_stored_without_asking(conn: Conn, tmp_path: Path) -> None:
    await _store_report(conn, tmp_path)
    await conn.execute(
        "update holdings_raw set cusip = '037833101' where accession_no = %s and line_no = 1",
        [REPORT],
    )
    fake = FakeOpenFigi()

    report = await _map(conn, tmp_path, fake)

    assert report.invalid == 1
    assert "037833101" not in [cusip for batch in fake.asked for cusip in batch]
    [row] = await cusips.get_many(conn, ["037833101"])
    assert (row.status, row.note) == ("unmatched", "not a CUSIP: the check digit is wrong")


async def test_a_report_a_watch_routed_queues_the_mapping(conn: Conn, tmp_path: Path) -> None:
    # Live or caught up late (D-135): either way a watch found it, not the funds backfill.
    await _store_report(conn, tmp_path, routed=True)

    [task] = await queue.list_tasks(conn, kind=CUSIP_MAP)
    assert (task.key, task.status) == (PENDING, "queued")
