"""E3-S6: `service.filings.recent_filings` returns each filing with its events. The same
Schedule 13D (Disney's on FuboTV) is listed under both companies; only FuboTV's listing gets
the activist label (D-061). E5a: by ticker, a page at a time. D-139: a form asked for matches
its family, in any spelling; a 13D or 13G only on the listing of the company it is about."""

from datetime import date
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
from marketdata.service.filings import FilingView, recent_filings

Conn = psycopg.AsyncConnection[Any]
FUBO, DIS = 1484769, 1744489
THIRTEEN_D = "0000950157-25-000917"
CHANGE_IN_CONTROL = "0001493152-25-020163"
# FuboTV's 13D family, newest first: Disney's SCHEDULE 13D/A and 13D, then the old codes.
FUBO_13D = [
    ("0000950157-26-000406", "SCHEDULE 13D/A"),
    (THIRTEEN_D, "SCHEDULE 13D"),
    ("0000905718-21-000563", "SC 13D/A"),
    ("0000905718-21-000511", "SC 13D"),
    ("0001493152-16-012005", "SC 13D"),
    ("0001493152-16-012004", "SC 13D"),
]
DISNEYS_OWN_13G = {  # Disney's stakes in other companies: its listings have no file number
    "0000950157-21-000161",
    "0001193125-20-310786",
    "0000950157-20-000558",
    "0001104659-20-046814",
}


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
    for cik, name, ticker in ((FUBO, "FuboTV Inc.", "FUBO"), (DIS, "Walt Disney Co", "DIS")):
        await conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) "
            "values (%s, %s, 'domestic', true) on conflict (cik) do nothing",
            [cik, name],
        )
        await conn.execute(
            "insert into securities (cik, ticker, valid_from) values (%s, %s, '2026-10-25')",
            [cik, ticker],
        )
    return conn


async def _filings(conn: Conn, ticker: str, **options: Any) -> tuple[FilingView, ...]:
    return (await recent_filings(conn, ticker, **options)).data.filings


async def test_a_13d_gets_the_activist_label_on_the_subjects_listing_only(watched: Conn) -> None:
    listed = await _filings(watched, "FUBO", form="SCHEDULE 13D")
    disneys = await _filings(watched, "dis", form="schedule 13d")

    [subject] = [filing for filing in listed if filing.accession_no == THIRTEEN_D]
    assert [(e.item_or_form, e.label, e.importance) for e in subject.events] == [
        ("SCHEDULE 13D", "An investor crossed 5% and may seek to influence the company", "high")
    ]
    assert subject.url == urls.filing_index_page(FUBO, THIRTEEN_D)
    # Disney's listing of its own stake in FuboTV: not one of Disney's 5% holders (D-139).
    assert disneys == ()


async def test_8k_items_are_labelled_in_order_without_hidden_ones(watched: Conn) -> None:
    listed = await _filings(watched, "FUBO", form="8-K", limit=100)

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
    listed = await _filings(watched, "FUBO", limit=10)

    assert len(listed) == 10
    assert [f.filed_at for f in listed] == sorted((f.filed_at for f in listed), reverse=True)
    with pytest.raises(InvalidParameter, match="limit must be from 1 to 100"):
        await recent_filings(watched, "FUBO", limit=101)


async def test_pages_follow_one_another_without_gaps(watched: Conn) -> None:
    first = (await recent_filings(watched, "FUBO", limit=7)).data
    assert first.next_cursor is not None
    second = (await recent_filings(watched, "FUBO", limit=7, cursor=first.next_cursor)).data
    both = (await recent_filings(watched, "FUBO", limit=14)).data

    assert [f.accession_no for f in (*first.filings, *second.filings)] == [
        f.accession_no for f in both.filings
    ]
    with pytest.raises(InvalidParameter, match="cursor is not one this endpoint returned"):
        await recent_filings(watched, "FUBO", cursor="bm90IGEgY3Vyc29y")


async def test_sources_name_each_filing_listed(watched: Conn) -> None:
    served = await recent_filings(watched, "FUBO", form="SCHEDULE 13D", since=date(2025, 11, 1))

    amendment, original = served.sources  # Disney's 13D/A and its 13D, newest first
    assert (original.accession_no, original.form) == (THIRTEEN_D, "SCHEDULE 13D")
    assert original.url == urls.filing_folder(FUBO, THIRTEEN_D)
    assert (amendment.accession_no, amendment.form) == FUBO_13D[0]
    assert served.as_of is not None and served.quality_flags == ()


async def _every(conn: Conn, ticker: str, **options: Any) -> list[FilingView]:
    """All of a company's filings that match, page after page."""
    listed: list[FilingView] = []
    cursor = None
    while True:
        page = (await recent_filings(conn, ticker, limit=100, cursor=cursor, **options)).data
        listed.extend(page.filings)
        if page.next_cursor is None:
            return listed
        cursor = page.next_cursor


NEW_13D_FIRST = ("SCHEDULE 13D", "SCHEDULE 13D/A", "SC 13D", "SC 13D/A")
OLD_13D_FIRST = ("SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A")


@pytest.mark.parametrize(
    ("asked", "matched"),
    [
        ("13D", NEW_13D_FIRST),
        ("13d", NEW_13D_FIRST),
        ("SCHEDULE 13D", NEW_13D_FIRST),
        ("Schedule 13D", NEW_13D_FIRST),
        ("SC 13D", OLD_13D_FIRST),
    ],
)
async def test_each_spelling_of_13d_lists_the_whole_family(
    watched: Conn, asked: str, matched: tuple[str, ...]
) -> None:
    served = await recent_filings(watched, "FUBO", form=asked)

    assert [(f.accession_no, f.form) for f in served.data.filings] == FUBO_13D
    assert served.data.matched_forms == matched


async def test_13g_amendments_asked_for_come_with_their_hidden_label(watched: Conn) -> None:
    listed = await _filings(watched, "FUBO", form="SCHEDULE 13G", limit=100)

    assert len(listed) == 16 + 21 + 1 + 7  # SC 13G, SC 13G/A, SCHEDULE 13G, SCHEDULE 13G/A
    labels = {(f.form, e.label, e.importance) for f in listed for e in f.events}
    assert labels == {
        ("SC 13G", "A passive investor holds more than 5%", "normal"),
        ("SCHEDULE 13G", "A passive investor holds more than 5%", "normal"),
        ("SC 13G/A", "A passive 5% holder changed its stake", "hidden"),
        ("SCHEDULE 13G/A", "A passive 5% holder changed its stake", "hidden"),
    }
    assert all(len(f.events) == 1 for f in listed)
    # Without a form, a 13G amendment is listed without its label (§9.6).
    amendments = [f for f in await _every(watched, "FUBO") if f.form.endswith("13G/A")]
    assert len(amendments) == 28
    assert all(f.events == () for f in amendments)


@pytest.mark.parametrize(
    ("asked", "forms", "count"),
    [
        ("13D/A", {"SCHEDULE 13D/A", "SC 13D/A"}, 2),
        ("schedule 13g/a", {"SCHEDULE 13G/A", "SC 13G/A"}, 28),
        ("8-K/A", {"8-K/A"}, 11),
        ("10-K/A", {"10-K/A"}, 3),
    ],
)
async def test_an_amendment_asked_for_lists_amendments_only(
    watched: Conn, asked: str, forms: set[str], count: int
) -> None:
    listed = await _every(watched, "FUBO", form=asked)

    assert {f.form for f in listed} == forms
    assert len(listed) == count


@pytest.mark.parametrize(("asked", "count"), [("8-K", 172 + 11), ("10-K", 13 + 3), ("4", 153 + 9)])
async def test_a_form_includes_its_amendments(watched: Conn, asked: str, count: int) -> None:
    listed = await _every(watched, "FUBO", form=asked)

    assert {f.form for f in listed} == {asked, f"{asked}/A"}
    assert len(listed) == count
    assert len({f.accession_no for f in listed}) == count  # pages neither repeat nor skip


async def test_an_8k_amendment_comes_with_the_8ks(watched: Conn) -> None:
    listed = await _filings(watched, "FUBO", form="8-K", since=date(2025, 12, 1))

    assert ("0001493152-25-029112", "8-K/A") in {(f.accession_no, f.form) for f in listed}


async def test_a_companys_own_stakes_in_others_are_left_out(watched: Conn) -> None:
    """Disney's listing names the 13Gs about Disney (005-91350) and Disney's own stakes in other
    companies (no file number): only the first are Disney's 5% holders (D-061, D-139)."""
    listed = await _every(watched, "DIS", form="13G")

    assert len(listed) == 12
    assert not DISNEYS_OWN_13G & {f.accession_no for f in listed}
    assert listed[0].accession_no == "0002100119-26-001267"  # SCHEDULE 13G, 2026-04-30
    assert listed[1].accession_no == "0000102909-26-002686"  # SCHEDULE 13G/A, 2026-03-27
    assert [e.importance for e in listed[1].events] == ["hidden"]
    # Without a form every listing comes, Disney's own stakes too (D-098), unlabelled.
    own = [f for f in await _every(watched, "DIS") if f.accession_no in DISNEYS_OWN_13G]
    assert len(own) == 4
    assert all(f.events == () for f in own)


@pytest.mark.parametrize("asked", ["8-K, 10-Q", "13D;13G", "/A"])
async def test_text_that_isnt_one_form_is_refused(watched: Conn, asked: str) -> None:
    with pytest.raises(InvalidParameter, match="one form at a time"):
        await recent_filings(watched, "FUBO", form=asked)


async def test_a_blank_form_is_no_form(watched: Conn) -> None:
    blank = (await recent_filings(watched, "FUBO", form="  ", limit=5)).data
    every = (await recent_filings(watched, "FUBO", limit=5)).data

    assert blank.matched_forms is None
    assert blank.filings == every.filings
