"""E3-S6: `service.filings.recent_filings` returns each filing with its events. The same
Schedule 13D (Disney's on FuboTV) is listed under both companies; only FuboTV's listing gets
the activist label (D-061)."""

from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, connect_to, sec_serving

from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.errors import InvalidParameter
from marketdata.service.filings import recent_filings

Conn = psycopg.AsyncConnection[Any]
FUBO, DIS = 1484769, 1744489
THIRTEEN_D = "0000950157-25-000917"
CHANGE_IN_CONTROL = "0001493152-25-020163"


@pytest.fixture
async def watched(conn: Conn, tmp_path: Path) -> Conn:
    routes: dict[str, bytes | int] = {
        urls.submissions(FUBO): (SEC_FIXTURES / "submissions" / "FUBO.json").read_bytes(),
        urls.submissions(DIS): (SEC_FIXTURES / "submissions" / "DIS.json").read_bytes(),
    }
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        watcher = FilingsWatcher(fetcher, connect_to(conn), frozenset())
        await watcher.watch(FUBO)
        await watcher.watch(DIS)
    return conn


async def test_a_13d_gets_the_activist_label_on_the_subjects_listing_only(watched: Conn) -> None:
    [subject] = await recent_filings(watched, FUBO, form="SCHEDULE 13D")
    [filer] = await recent_filings(watched, DIS, form="SCHEDULE 13D")

    assert subject.accession_no == filer.accession_no == THIRTEEN_D
    assert [(e.item_or_form, e.label, e.importance) for e in subject.events] == [
        ("SCHEDULE 13D", "An investor crossed 5% and may seek to influence the company", "high")
    ]
    assert filer.events == ()
    assert subject.url == urls.filing_index_page(FUBO, THIRTEEN_D)


async def test_8k_items_are_labelled_in_order_without_hidden_ones(watched: Conn) -> None:
    listed = await recent_filings(watched, FUBO, form="8-K", limit=100)

    [filing] = [f for f in listed if f.accession_no == CHANGE_IN_CONTROL]
    assert [(e.item_or_form, e.importance) for e in filing.events] == [
        ("1.01", "normal"),
        ("2.01", "high"),
        ("3.02", "normal"),
        ("3.03", "normal"),
        ("5.01", "high"),
        ("5.02", "normal"),
        ("5.03", "normal"),
        ("8.01", "normal"),
    ]  # 9.01 is hidden
    assert filing.accepted_at is not None


async def test_filings_come_newest_first_within_the_limit(watched: Conn) -> None:
    listed = await recent_filings(watched, FUBO, limit=10)

    assert len(listed) == 10
    assert [f.filed_at for f in listed] == sorted((f.filed_at for f in listed), reverse=True)
    with pytest.raises(InvalidParameter, match="limit must be from 1 to 100"):
        await recent_filings(watched, FUBO, limit=101)
