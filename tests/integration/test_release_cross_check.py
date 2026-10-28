"""E4b-S6: each earnings release's slots are cross-checked against the 10-Q or 10-K for the same
quarter when its facts are loaded, and again whenever the release is stored (D-028). The SEC
is a mock serving the committed filings and Apple's companyfacts."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from conftest import SEC_FIXTURES, connect_to, filing_routes, sec_serving

from marketdata.db.repositories import earnings, value_changes
from marketdata.ingest.cross_check import cross_check_company
from marketdata.ingest.earnings import EarningsIngest
from marketdata.ingest.facts import FactsLoader
from marketdata.ingest.fetch import RawFetcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore

Conn = psycopg.AsyncConnection[Any]

AAPL = 320193
Q3_RELEASE = "0000320193-26-000018"  # fiscal 2026 Q3, quarter to 2026-06-27
Q4_RELEASE = "0000320193-25-000077"  # fiscal 2025 Q4, quarter to 2025-09-27
TEN_Q = "0000320193-26-000020"
TEN_K = "0000320193-25-000079"
FACTS = (SEC_FIXTURES / "companyfacts" / "AAPL-20261028.json").read_bytes()
M = Decimal(1_000_000)


async def _company(conn: Conn) -> None:
    await conn.execute(
        "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, in_universe) "
        "values (%s, 'Apple Inc.', '3571', '0926', 'domestic', true)",
        [AAPL],
    )


async def _release(conn: Conn, tmp_path: Path, accession: str) -> None:
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await EarningsIngest(fetcher, connect_to(conn)).run(AAPL, accession)


async def _facts(conn: Conn, tmp_path: Path, data: bytes = FACTS) -> None:
    async with sec_serving({urls.companyfacts(AAPL): data}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await FactsLoader(fetcher, connect_to(conn)).refresh(AAPL)


def _changed_revenue(data: bytes, value: int) -> bytes:
    """Apple's file with the 10-Q's third-quarter revenue set to `value`."""
    document = json.loads(data)
    rows = document["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]
    for row in rows["units"]["USD"]:
        if (row.get("start"), row["end"], row["accn"]) == ("2026-03-29", "2026-06-27", TEN_Q):
            row["val"] = value
    return json.dumps(document).encode()


async def _slots(conn: Conn, accession: str) -> dict[str, earnings.StoredFact]:
    return {fact.slot: fact for fact in await earnings.facts(conn, accession)}


async def test_a_release_is_pending_until_its_10q_is_loaded_then_matched(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn)
    await _release(conn, tmp_path, Q3_RELEASE)
    assert {f.cross_check_status for f in (await _slots(conn, Q3_RELEASE)).values()} == {"pending"}

    await _facts(conn, tmp_path)

    slots = await _slots(conn, Q3_RELEASE)
    assert {slot: fact.cross_check_status for slot, fact in slots.items()} == {
        "revenue": "matched",
        "operating_income": "matched",
        "net_income": "matched",
        "eps_diluted": "matched",
    }
    revenue = slots["revenue"]
    assert (revenue.value, revenue.cross_check_value, revenue.cross_check_accession) == (
        Decimal(109_417) * M,
        Decimal(109_417) * M,
        TEN_Q,
    )
    assert "release_vs_filing_mismatch" not in revenue.quality_flags


async def test_a_quarter_whose_numbers_changed_between_release_and_filing(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn)
    await _release(conn, tmp_path, Q3_RELEASE)

    await _facts(conn, tmp_path, _changed_revenue(FACTS, 105_000_000_000))

    revenue = (await _slots(conn, Q3_RELEASE))["revenue"]
    assert (revenue.cross_check_status, revenue.cross_check_value) == (
        "mismatch",
        Decimal(105_000) * M,
    )
    assert "release_vs_filing_mismatch" in revenue.quality_flags
    assert revenue.value == Decimal(109_417) * M  # a mismatch never changes the release value
    assert await value_changes.for_accession(conn, Q3_RELEASE) == []

    # Stored again (a re-run reads the raw store): the check runs again and keeps the flag.
    await _release(conn, tmp_path, Q3_RELEASE)
    again = (await _slots(conn, Q3_RELEASE))["revenue"]
    assert (again.cross_check_status, again.quality_flags) == ("mismatch", revenue.quality_flags)


async def test_a_fourth_quarter_matches_the_derived_quarter_and_eps_is_not_comparable(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn)
    await _release(conn, tmp_path, Q4_RELEASE)
    await _facts(conn, tmp_path)

    slots = await _slots(conn, Q4_RELEASE)

    # The 10-K's year less the nine months; the 10-K reports no fourth-quarter EPS (§9.4 rule 5).
    assert (slots["revenue"].cross_check_status, slots["revenue"].cross_check_accession) == (
        "matched",
        TEN_K,
    )
    assert slots["revenue"].cross_check_value == Decimal(102_466) * M
    assert (slots["eps_diluted"].cross_check_status, slots["eps_diluted"].cross_check_value) == (
        "not_comparable",
        None,
    )


async def test_a_second_check_writes_nothing(conn: Conn, tmp_path: Path) -> None:
    await _company(conn)
    await _release(conn, tmp_path, Q3_RELEASE)
    await _facts(conn, tmp_path)

    report = await cross_check_company(connect_to(conn), AAPL)

    assert (report.releases, report.written) == (1, 0)
    assert report.statuses == {"matched": 4}
