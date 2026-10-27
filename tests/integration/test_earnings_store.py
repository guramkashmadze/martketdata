"""E7a-S4: an earnings task extracts, verifies and stores a release; a re-run changes nothing
unless the rules or overrides change a served value (a correction), and an 8-K/A that changes
a value records an amendment (D-027). The SEC is a mock serving the committed filings."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from conftest import connect_to, filing_routes, sec_serving

from marketdata.db.repositories import earnings, value_changes
from marketdata.extract.earnings_rules import RULES_VERSION, Override, parse_overrides
from marketdata.ingest.earnings import EarningsIngest, ExtractedRelease
from marketdata.ingest.fetch import RawFetcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore

Conn = psycopg.AsyncConnection[Any]

AAPL, JPM = 320193, 19617
RELEASE = "0000320193-26-000018"
AMENDMENT = "0000320193-26-000019"  # crafted from RELEASE: an 8-K/A with one value changed
EXHIBIT = "a8-kex991q3202606272026.htm"
M = Decimal(1_000_000)


async def _company(conn: Conn, cik: int, sic: str, fiscal_year_end: str) -> None:
    await conn.execute(
        "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, in_universe) "
        "values (%s, 'test', %s, %s, 'domestic', true)",
        [cik, sic, fiscal_year_end],
    )


async def _run(
    conn: Conn,
    tmp_path: Path,
    cik: int,
    accession: str,
    overrides: tuple[Override, ...] = (),
    routes: dict[str, bytes | int] | None = None,
) -> ExtractedRelease:
    async with sec_serving(routes if routes is not None else filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        ingest = EarningsIngest(fetcher, connect_to(conn), overrides)
        return await ingest.run(cik, accession)


async def test_a_release_is_extracted_verified_and_stored(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL, "3571", "0926")

    run = await _run(conn, tmp_path, AAPL, RELEASE)

    stored = await earnings.get_release(conn, RELEASE)
    assert stored is not None
    assert (stored.status, stored.fiscal_year, stored.fiscal_period) == ("complete", 2026, "Q3")
    assert (stored.period_end, stored.doc_name) == (date(2026, 6, 27), EXHIBIT)
    assert stored.accepted_at == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    assert stored.verified_at is not None
    assert (stored.extractor, stored.extractor_version) == ("rules", RULES_VERSION)
    assert stored.check_report["accepted_at"]["index_page"] == "2026-07-30T20:30:28+00:00"
    facts = {fact.slot: fact for fact in await earnings.facts(conn, RELEASE)}
    assert {slot: fact.check_status for slot, fact in facts.items()} == {
        "revenue": "passed",
        "operating_income": "passed",
        "net_income": "passed",
        "eps_diluted": "passed",
    }
    revenue = facts["revenue"]
    assert (revenue.value, revenue.prior_value, revenue.change_pct) == (
        Decimal(109_417) * M,
        Decimal(94_036) * M,
        Decimal("16.4"),
    )
    assert revenue.source_quote is not None and "| 109,417 |" in revenue.source_quote
    assert revenue.cross_check_status == "pending"
    assert run.changes == ()


async def test_a_rerun_with_the_same_rules_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL, "3571", "0926")
    first = await _run(conn, tmp_path, AAPL, RELEASE)
    verified = (await earnings.get_release(conn, RELEASE)).verified_at  # type: ignore[union-attr]

    again = await _run(conn, tmp_path, AAPL, RELEASE, routes={})  # no SEC request (D-070)

    assert again.changes == ()
    assert again.record.facts == first.record.facts
    assert await value_changes.for_accession(conn, RELEASE) == []
    stored = await earnings.get_release(conn, RELEASE)
    assert stored is not None and stored.verified_at == verified  # the first verification


async def test_an_override_that_changes_a_value_records_a_correction(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL, "3571", "0926")
    await _run(conn, tmp_path, AAPL, RELEASE)
    overrides = parse_overrides(
        '[[override]]\ncik = 320193\nslot = "operating_income"\nrow_label = "Gross margin"\n'
        'reason = "test: a pinned row"\n'
        f'source_url = "{urls.filing_document(AAPL, RELEASE, EXHIBIT)}"\n'
    )

    run = await _run(conn, tmp_path, AAPL, RELEASE, overrides, routes={})

    changes = await value_changes.for_accession(conn, RELEASE)
    assert [(c.entity_key["slot"], c.entity_key["field"], c.reason) for c in changes] == [
        ("operating_income", "value", "correction"),
        ("operating_income", "prior_value", "correction"),
    ]
    assert (changes[0].old_value, changes[0].new_value) == (
        Decimal(35_695) * M,
        Decimal(54_770) * M,
    )
    assert changes[0].cik == AAPL
    assert run.record.check_report["overrides"] == ["operating_income: row 'Gross margin'"]


async def test_an_8k_a_that_changes_a_value_records_an_amendment(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL, "3571", "0926")
    await _run(conn, tmp_path, AAPL, RELEASE)
    routes = filing_routes()
    page = routes[urls.filing_index_page(AAPL, RELEASE)]
    exhibit = routes[urls.filing_document(AAPL, RELEASE, EXHIBIT)]
    assert isinstance(page, bytes) and isinstance(exhibit, bytes)
    routes[urls.filing_index_page(AAPL, AMENDMENT)] = (
        page.replace(RELEASE.encode(), AMENDMENT.encode())
        .replace(RELEASE.replace("-", "").encode(), AMENDMENT.replace("-", "").encode())
        .replace(b"<strong>Form 8-K</strong>", b"<strong>Form 8-K/A</strong>")
    )
    routes[urls.filing_document(AAPL, AMENDMENT, EXHIBIT)] = exhibit.replace(
        b"29,789", b"29,790", 1
    )  # the first net income figure: the quarter's

    run = await _run(conn, tmp_path, AAPL, AMENDMENT, routes=routes)

    assert run.record.fact("net_income").value == Decimal(29_790) * M
    [change] = await value_changes.for_accession(conn, AMENDMENT)
    assert change.entity_key == {
        "accession_no": AMENDMENT,
        "slot": "net_income",
        "field": "value",
        "amends": RELEASE,
    }
    assert (change.old_value, change.new_value, change.reason) == (
        Decimal(29_789) * M,
        Decimal(29_790) * M,
        "amendment",
    )
    original = await earnings.get_release(conn, RELEASE)
    assert original is not None and original.status == "complete"  # it stays stored


async def test_a_bank_has_no_revenue_slot(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, JPM, "6021", "1231")

    run = await _run(conn, tmp_path, JPM, "0001628280-26-065582")

    assert run.record.fact("revenue").check_status == "not_applicable"
    assert run.record.status == "partial"  # JPMorgan prints no operating income line
    assert run.record.period_end == date(2026, 9, 30)


async def test_a_filing_without_an_earnings_exhibit_has_no_statement(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, 753308, "4911", "1231")

    run = await _run(conn, tmp_path, 753308, "0000753308-26-000004")

    assert run.record.status == "no_statement"
    stored = await earnings.get_release(conn, "0000753308-26-000004")
    assert stored is not None and stored.verified_at is None
