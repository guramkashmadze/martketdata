"""E7a-S1: an earnings task finds the press release through the filing's index page, keeps the
raw files first and each exhibit's text in `documents`. A re-run reads the raw store and makes
no SEC request (D-070). Failures and differences are logged with the accession number.

The SEC is a mock serving the committed filings: the last four earnings 8-Ks of Apple
(September year end), Target (EX-99, January year end), lululemon (thousands), JPMorgan (a
bank) and Costco (12- and 16-week quarters).
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import connect_to, filing_routes, sec_serving
from structlog.testing import capture_logs

from marketdata.db.repositories import documents, filings, raw_objects
from marketdata.ingest.earnings import EarningsIngest, FetchedRelease
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.filings import FilingRow
from marketdata.sec.client import SecNotFound
from marketdata.sec.raw_store import LocalRawStore

Conn = psycopg.AsyncConnection[Any]

AAPL, TGT, LULU, JPM, COST, NEE = 320193, 27419, 1397187, 19617, 909832, 753308

# The last four earnings 8-Ks of each company, with the exhibits tried, in §9.9's order.
LAST_FOUR = [
    (AAPL, "0000320193-25-000077", [("EX-99.1", "a8-kex991q4202509272025.htm")]),
    (AAPL, "0000320193-26-000005", [("EX-99.1", "a8-kex991q1202612272025.htm")]),
    (AAPL, "0000320193-26-000011", [("EX-99.1", "a8-kex991q2202603282026.htm")]),
    (AAPL, "0000320193-26-000018", [("EX-99.1", "a8-kex991q3202606272026.htm")]),
    (TGT, "0000027419-25-000123", [("EX-99", "a2025q3ex-99.htm")]),
    (TGT, "0000027419-26-000012", [("EX-99", "a2025q4ex-99.htm")]),
    (TGT, "0000027419-26-000020", [("EX-99", "a2026q1ex-99.htm")]),
    (TGT, "0000027419-26-000034", [("EX-99", "a2026q2ex-99.htm")]),
    (
        LULU,
        "0001397187-25-000054",
        [("EX-99.1", "lulu-20251102xex991.htm"), ("EX-99.2", "lulu-20251211xex992.htm")],
    ),
    (LULU, "0001397187-26-000019", [("EX-99.1", "lulu-20260201xex991.htm")]),
    (LULU, "0001397187-26-000077", [("EX-99.1", "lulu-20260503xex991.htm")]),
    (LULU, "0001397187-26-000126", [("EX-99.1", "lulu-20260802xex991.htm")]),
    *[
        (
            JPM,
            accession,
            [
                ("EX-99.1", f"a{quarter}erfexhibit991narrative.htm"),
                ("EX-99.2", f"a{quarter}erfex992supplement.htm"),
            ],
        )
        for accession, quarter in [
            ("0001628280-26-001902", "4q25"),
            ("0001628280-26-024990", "1q26"),
            ("0001628280-26-048078", "2q26"),
            ("0001628280-26-065582", "3q26"),
        ]
    ],
    *[
        (
            COST,
            accession,
            [("EX-99.1", f"costex9918-k{day}.htm"), ("EX-99.2", f"costex9928-k{day}.htm")],
        )
        for accession, day in [
            ("0000909832-25-000164", "121125"),
            ("0000909832-26-000025", "3526"),
            ("0000909832-26-000046", "52826"),
            ("0000909832-26-000084", "92426"),
        ]
    ],
]


async def _fetch(
    conn: Conn, tmp_path: Path, cik: int, accession: str, routes: dict[str, bytes | int] | None
) -> FetchedRelease:
    async with sec_serving(filing_routes() if routes is None else routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await EarningsIngest(fetcher, connect_to(conn)).fetch(cik, accession)


@pytest.mark.parametrize(("cik", "accession", "exhibits"), LAST_FOUR)
async def test_the_press_release_is_found_through_the_index_page_and_stored(
    conn: Conn, tmp_path: Path, cik: int, accession: str, exhibits: list[tuple[str, str]]
) -> None:
    release = await _fetch(conn, tmp_path, cik, accession, None)

    assert release.index.items[0] == "2.02"
    assert [(doc.doc_type, doc.doc_name) for doc in release.documents] == exhibits
    assert release.fetched == 1 + len(exhibits)  # the index page, then each exhibit
    stored = {doc.doc_name: doc for doc in await documents.for_filing(conn, accession)}
    assert set(stored) == {name for _, name in exhibits}
    for doc_type, name in exhibits:
        row = stored[name]
        assert (row.doc_type, row.raw_key) == (doc_type, f"raw/sec/{cik}/{accession}/{name}")
        assert row.text == next(doc.text for doc in release.documents if doc.doc_name == name)
        assert await raw_objects.get(conn, row.raw_key) is not None
    index_key = f"raw/sec/{cik}/{accession}/{accession}-index.htm"
    assert await raw_objects.get(conn, index_key) is not None
    # The press release's statements are tables, written as markdown rows.
    press_release = release.documents[0]
    assert press_release.tables
    assert any(line.startswith("| ") for line in press_release.text.splitlines())


async def test_the_text_holds_the_statement_rows_as_printed(conn: Conn, tmp_path: Path) -> None:
    release = await _fetch(conn, tmp_path, AAPL, "0000320193-26-000018", None)

    [document] = await documents.for_filing(conn, "0000320193-26-000018")
    assert document.text.startswith("Exhibit 99.1\nApple reports third quarter results")
    assert "| Net income | $ 29,789 | $ 23,434 | $ 101,464 | $ 84,544 |" in document.text
    assert "<DOCUMENT>" not in document.text  # EDGAR's SGML wrapper is not part of the text
    assert release.index.accepted_at == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)


async def test_a_rerun_reads_the_raw_store_and_makes_no_request(conn: Conn, tmp_path: Path) -> None:
    first = await _fetch(conn, tmp_path, JPM, "0001628280-26-065582", None)

    again = await _fetch(conn, tmp_path, JPM, "0001628280-26-065582", {})  # any request: 404

    assert (first.fetched, again.fetched) == (3, 0)
    assert again.documents == first.documents


async def test_an_index_page_time_unlike_the_listing_is_logged(conn: Conn, tmp_path: Path) -> None:
    accession = "0000320193-26-000018"
    listed = datetime(2026, 7, 31, 0, 30, 28, tzinfo=UTC)  # 4 hours late, as a shifted file
    await filings.insert_new(conn, [_listing(AAPL, accession, listed)])

    with capture_logs() as logs:
        release = await _fetch(conn, tmp_path, AAPL, accession, None)

    assert release.listed_accepted_at == listed
    [event] = [log for log in logs if log["event"] == "release_accepted_at_differs"]
    assert event["accession_no"] == accession
    assert event["index_page"] == "2026-07-30T20:30:28+00:00"


async def test_a_listing_with_the_same_time_logs_nothing(conn: Conn, tmp_path: Path) -> None:
    accession = "0000320193-26-000018"
    accepted = datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    await filings.insert_new(conn, [_listing(AAPL, accession, accepted)])

    with capture_logs() as logs:
        await _fetch(conn, tmp_path, AAPL, accession, None)

    assert [log["event"] for log in logs if log["log_level"] == "warning"] == []


async def test_a_filing_without_an_earnings_exhibit_is_logged(conn: Conn, tmp_path: Path) -> None:
    accession = "0000753308-26-000004"  # NextEra: items 2.02 and 7.01, no exhibits

    with capture_logs() as logs:
        release = await _fetch(conn, tmp_path, NEE, accession, None)

    assert release.exhibits == ()
    assert await documents.for_filing(conn, accession) == []
    [event] = [log for log in logs if log["event"] == "earnings_exhibit_not_found"]
    assert event["accession_no"] == accession


async def test_an_exhibit_of_page_pictures_is_logged(conn: Conn, tmp_path: Path) -> None:
    accession = "0000909832-26-000084"  # Costco's EX-99.2: nine pictures, no tables

    with capture_logs() as logs:
        release = await _fetch(conn, tmp_path, COST, accession, None)

    assert [len(doc.tables) > 0 for doc in release.documents] == [True, False]
    [event] = [log for log in logs if log["event"] == "exhibit_is_page_images"]
    assert (event["accession_no"], event["doc_name"]) == (accession, "costex9928-k92426.htm")


async def test_a_missing_exhibit_fails_the_attempt_so_the_task_retries(
    conn: Conn, tmp_path: Path
) -> None:
    routes = filing_routes()
    del routes[
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/"
        "a8-kex991q3202606272026.htm"
    ]

    with pytest.raises(SecNotFound):
        await _fetch(conn, tmp_path, AAPL, "0000320193-26-000018", routes)

    assert await documents.for_filing(conn, "0000320193-26-000018") == []


def _listing(cik: int, accession: str, accepted: datetime) -> FilingRow:
    return FilingRow(
        accession_no=accession,
        cik=cik,
        form="8-K",
        filed_at=date(2026, 7, 30),
        accepted_at=accepted,
        period_of_report=date(2026, 7, 30),
        items_8k=("2.02", "9.01"),
        primary_doc="aapl-20260730.htm",
        is_xbrl=True,
        file_number="001-36743",
    )
