"""E7a-S5: `marketdata earnings show <TICKER>` prints a company's latest release with each
value's proof, a predecessor's releases included; `marketdata extract report` counts the stored
releases. Releases are stored by the earnings task, against a mock SEC serving the committed
filings, inside the test's transaction."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import connect_to, filing_routes, sec_serving

from marketdata.cli import _release_lines, _report_lines
from marketdata.ingest.earnings import EarningsIngest
from marketdata.ingest.fetch import RawFetcher
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.companies import ResolvedSecurity
from marketdata.service.earnings import latest_release
from marketdata.service.errors import NotFound
from marketdata.service.extraction import extraction_report

Conn = psycopg.AsyncConnection[Any]

AAPL, XOM, EXXON = 320193, 2115436, 34088  # 34088: Exxon Mobil Corp, XOM's predecessor
Q4_2025, Q3_2026 = "0000320193-25-000077", "0000320193-26-000018"


async def _company(conn: Conn, cik: int, fiscal_year_end: str) -> None:
    await conn.execute(
        "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, in_universe) "
        "values (%s, 'test', '3571', %s, 'domestic', true)",
        [cik, fiscal_year_end],
    )


async def _run(conn: Conn, tmp_path: Path, cik: int, accession: str) -> None:
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await EarningsIngest(fetcher, connect_to(conn)).run(cik, accession)


def _security(ticker: str, cik: int, name: str) -> ResolvedSecurity:
    return ResolvedSecurity(
        ticker=ticker,
        cik=cik,
        name=name,
        filer_type="domestic",
        in_universe=True,
        security_type="common",
        share_class=None,
        exchange="Nasdaq",
        listed_since=date(2026, 10, 24),
    )


async def test_the_latest_release_shows_each_value_with_its_proof(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL, "0926")
    await _run(conn, tmp_path, AAPL, Q3_2026)
    await _run(conn, tmp_path, AAPL, Q4_2025)

    release = await latest_release(conn, AAPL)

    assert release.accession_no == Q3_2026  # accepted last, though stored first
    revenue = release.facts[0]
    assert (revenue.slot, revenue.check_status, revenue.checks) == (
        "revenue",
        "passed",
        ("in_source", "scale", "sanity"),
    )
    lines = _release_lines(_security("AAPL", AAPL, "Apple Inc."), release)
    assert lines[:3] == [
        "Apple Inc. (AAPL, CIK 320193)",
        f"  Release    8-K {Q3_2026}, accepted 2026-07-30 20:30:28 UTC",
        "  Quarter    Q3 fiscal 2026, ended 2026-06-27 (columns: Three Months Ended June 27, 2026)",
    ]
    revenue = "revenue: 109,417,000,000 USD, prior-year quarter 94,036,000,000 USD"
    eps = "eps_diluted: 2.02 USD per share, prior-year quarter 1.57 USD per share"
    assert f"{revenue}, change +16.4%" in lines
    assert "  row        | Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |" in lines
    assert f"{eps}, change +28.7%" in lines
    assert lines[-1] == "Information from public filings. Not investment advice."


async def test_a_predecessors_release_is_the_companys(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, XOM, "1231")
    await _company(conn, EXXON, "1231")
    await conn.execute(
        "insert into company_predecessors (cik, predecessor_cik, source_url, note) "
        "values (%s, %s, 'https://example.test/8-K12B', 'test')",
        [XOM, EXXON],
    )
    await _run(conn, tmp_path, EXXON, "0000034088-26-000065")

    release = await latest_release(conn, XOM)

    assert (release.cik, release.status) == (EXXON, "partial")  # no operating income line
    lines = _release_lines(_security("XOM", XOM, "Exxon Mobil Holdings Corp"), release)
    assert lines[1].endswith(", filed by its predecessor CIK 34088")


async def test_a_company_without_releases_has_none_to_show(conn: Conn) -> None:
    await _company(conn, AAPL, "0926")

    with pytest.raises(NotFound, match="No earnings release of CIK 320193 is stored"):
        await latest_release(conn, AAPL)


async def test_the_report_counts_the_stored_releases(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, AAPL, "0926")
    await conn.execute(
        "insert into securities (cik, ticker, valid_from) values (%s, 'AAPL', '2026-10-24')", [AAPL]
    )
    await _run(conn, tmp_path, AAPL, Q3_2026)
    await _run(conn, tmp_path, AAPL, Q4_2025)

    report = await extraction_report(conn)

    assert report.statuses["complete"] == 2
    [apple] = [company for company in report.companies if company.cik == AAPL]
    assert (apple.releases, apple.slots.passed, apple.slots.hit_rate) == (2, 8, 100)
    assert any(line.startswith("  AAPL    320193   test") for line in _report_lines(report))
