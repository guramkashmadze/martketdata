"""E5b-S4: the accuracy report (PLAN.md §9.10) from the data as stored: the golden-set match rate
per slot and metric, the cross-check match rate, the extraction pass rate, the minutes from SEC
acceptance to verified for releases processed live, and value changes by reason; published by
`marketdata quality accuracy` and, without a key, by GET /v1/status."""

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
import structlog
from api_contract import check_envelope
from api_seed import ACME, BETA, FUND, OLD_Q1_RELEASE, Q2_RELEASE, Q3_RELEASE, seed
from typer.testing import CliRunner

from marketdata import cli
from marketdata.db.repositories import golden
from marketdata.normalize.golden import GoldenValue
from marketdata.service.accuracy import accuracy_report

Conn = psycopg.AsyncConnection[Any]
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
URL = "https://www.sec.gov/Archives/edgar/data/1001/000100100026000020/ex99.htm"


def _release(accession: str, slot: str, value: str | None, prior: str | None, status: str) -> Any:
    return GoldenValue(
        kind="earnings",
        cik=ACME,
        accession_no=accession,
        slot=slot,
        metric=None,
        period_type=None,
        period_end=None,
        value=Decimal(value) if value is not None else None,
        prior_value=Decimal(prior) if prior is not None else None,
        status=status,  # type: ignore[arg-type]
        note="a test difference" if status == "known_difference" else None,
        source_url=URL,
        source_file="golden/earnings/acme.toml",
    )


def _metric(cik: int, metric: str, period_type: str, end: str, value: str, status: str) -> Any:
    return GoldenValue(
        kind="metric",
        cik=cik,
        accession_no=None,
        slot=None,
        metric=metric,
        period_type=period_type,
        period_end=date.fromisoformat(end),
        value=Decimal(value),
        prior_value=None,
        status=status,  # type: ignore[arg-type]
        note=None,
        source_url=URL,
        source_file="golden/metrics/acme.toml",
    )


GOLDEN = [
    _release(Q2_RELEASE, "revenue", "1000", "950", "expected"),  # served exactly
    _release(Q2_RELEASE, "eps_diluted", "1.25", "1.10", "expected"),  # served exactly
    _release(Q3_RELEASE, "revenue", "1100", "1000", "expected"),  # failed: not served
    _release(Q2_RELEASE, "operating_income", None, None, "not_applicable"),
    _release("0001001000-26-000099", "revenue", "5", "4", "expected"),  # not stored here
    _metric(ACME, "revenue", "Q", "2026-06-30", "1000", "expected"),  # the company's row
    _metric(ACME, "revenue", "Q", "2026-03-31", "900", "expected"),  # its predecessor's row
    _metric(ACME, "net_income", "TTM", "2026-06-30", "401", "known_difference"),  # 400 served
    _metric(ACME, "operating_cash_flow", "TTM", "2026-06-30", "500", "expected"),  # null, gap
    _metric(BETA, "revenue", "FY", "2025-12-31", "4800", "expected"),  # no such row
    _metric(FUND, "revenue", "Q", "2026-06-30", "1", "expected"),  # no rows at all
]


@pytest.fixture
async def stored(conn: Conn) -> Conn:
    await seed(conn)
    await golden.replace_all(conn, GOLDEN)
    # Two releases the watcher routed when filed: verified 4 and 10 minutes after acceptance.
    for accession, minutes in ((Q2_RELEASE, 4), (OLD_Q1_RELEASE, 10)):
        await conn.execute(
            "update earnings_releases set live = true, "
            "verified_at = accepted_at + %s * interval '1 minute' where accession_no = %s",
            [minutes, accession],
        )
    return conn


async def test_the_report_counts_what_is_served_now(stored: Conn) -> None:
    report = await accuracy_report(stored, now=NOW)

    found = report.golden
    assert (found.values, found.not_applicable, found.not_stored) == (11, 1, 2)
    assert found.known_differences == 1
    assert (found.earnings.matched, found.earnings.checked) == (2, 3)
    assert found.earnings.rate == Decimal("66.7")
    assert (found.slots["revenue"].matched, found.slots["revenue"].checked) == (1, 2)
    assert found.slots["eps_diluted"].rate == Decimal("100.0")
    assert found.slots["net_income"].rate is None  # nothing checked
    assert (found.metrics_total.matched, found.metrics_total.checked) == (2, 5)
    assert found.metrics["revenue"].matched == 2 and found.metrics["net_income"].matched == 0
    checks = report.cross_check
    assert checks.statuses == {"matched": 1, "mismatch": 1, "not_comparable": 0, "pending": 2}
    assert checks.rate == Decimal("50.0")
    [mismatch] = checks.mismatches
    assert (mismatch.slot, mismatch.release_value, mismatch.filing_value) == (
        "eps_diluted",
        Decimal("1.25"),
        Decimal("1.20"),
    )
    extraction = report.extraction
    assert extraction.statuses["complete"] == 2 and extraction.statuses["failed"] == 1
    assert (extraction.total.matched, extraction.total.checked) == (3, 4)
    assert extraction.slots["revenue"].rate == Decimal("66.7")
    assert report.latency.releases == 2
    assert (report.latency.median_minutes, report.latency.p90_minutes) == (
        Decimal("7.0"),
        Decimal("9.4"),
    )
    assert report.changes.since == NOW - timedelta(days=90)
    assert report.changes.earnings_facts == {"correction": 1, "restatement": 0, "amendment": 1}
    assert report.changes.financial_metrics == {
        "correction": 0,
        "restatement": 1,
        "amendment": 0,
    }


async def test_backfilled_releases_dont_count_toward_the_time_to_verified(conn: Conn) -> None:
    await seed(conn)  # releases verified as accepted, but none routed live

    report = await accuracy_report(conn, now=NOW)

    assert (report.latency.releases, report.latency.median_minutes) == (0, None)
    assert report.golden.values == 0 and report.golden.earnings.rate is None


async def test_status_needs_no_key(api: httpx2.AsyncClient, stored: Conn) -> None:
    response = await api.get("/v1/status")
    again = await api.get("/v1/status")

    assert response.status_code == 200, response.text
    body = response.json(parse_float=Decimal)
    check_envelope(body)
    data = body["data"]
    assert data["freshness"]["live_releases"] == 2
    assert data["freshness"]["release_accepted_at"] == "2026-10-28T20:05:00Z"
    accuracy = data["accuracy"]
    assert accuracy["cross_check"]["rate"] == Decimal("50.0")
    assert accuracy["golden"]["earnings"] == {"matched": 2, "checked": 3, "rate": Decimal("66.7")}
    assert accuracy["latency"]["median_minutes"] == Decimal("7.0")
    assert again.json(parse_float=Decimal) == body  # reused for a minute
    logged = await (
        await stored.execute("select key_id, path, status from api_requests order by id")
    ).fetchall()
    assert logged == [(None, "/v1/status", 200), (None, "/v1/status", 200)]


EARNINGS_FILE = """
cik = 1001
fiscal_year_end = "1231"

[[release]]
accession_no = "0001001000-26-000020"
url = "https://www.sec.gov/Archives/edgar/data/1001/000100100026000020/ex99.htm"
status = "complete"
period_end = "not stated"
scale = "millions"
revenue = ["1,000", "950"]
operating_income = ["100", "90"]
net_income = ["80", "70"]
eps_diluted = ["1.25", "1.10"]
"""
METRICS_FILE = """
cik = 1001
fiscal_year_end = "1231"

[[period]]
type = "Q"
period_end = 2026-06-30
fiscal = "2026 Q2"
url = ["https://www.sec.gov/Archives/edgar/data/1001/000100100026000020/ex99.htm"]
revenue = "1,000"
"""


@pytest.fixture
def golden_dir(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    directory = tmp_path / "golden"
    (directory / "earnings").mkdir(parents=True)
    (directory / "metrics").mkdir()
    (directory / "earnings" / "acme.toml").write_text(EARNINGS_FILE)
    (directory / "metrics" / "acme.toml").write_text(METRICS_FILE)
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("GOLDEN_DIR", str(directory))
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield directory
    # The CLI set logging up for the process; a module logger first used after it would keep
    # this setup and slip past the log captures of later tests.
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from golden_values")


def test_the_command_loads_the_golden_files_and_prints_the_report(golden_dir: Path) -> None:
    result = CliRunner().invoke(cli.app, ["quality", "accuracy"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "Golden set: 5 hand-checked values from 2 files, loaded " in out
    assert "not stored in this database: 5" in out  # the test database holds no data
    assert "Earnings releases against their 10-Qs and 10-Ks (D-028)" in out
    assert "Minutes from SEC acceptance to verified, releases processed live: none yet" in out
    assert out.rstrip().endswith("Information from public filings. Not investment advice.")

    (golden_dir / "metrics" / "acme.toml").write_text("not toml")
    broken = CliRunner().invoke(cli.app, ["quality", "accuracy"])
    kept = CliRunner().invoke(cli.app, ["quality", "accuracy", "--no-load"])

    assert broken.exit_code == 2 and "golden/metrics/acme.toml" in broken.output
    assert kept.exit_code == 0 and "Golden set: 5 hand-checked values loaded" in kept.output
