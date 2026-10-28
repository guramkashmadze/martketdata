"""E4b-S3: `marketdata quality report` lists the flags on stored metric rows by company and
metric, and the companies whose latest 10-Q or 10-K is more than 120 days old; a metric
series marks a stale company's newest value (§9.5 stale_data, decided when read)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli
from marketdata.service.financials import metric_series

Conn = psycopg.AsyncConnection[Any]
FRESH, OLD = 900001, 900002
INSERT = (
    "insert into financial_metrics (cik, metric, period_type, fiscal_year, fiscal_period, "
    "period_start, period_end, value, unit, method, concept, accession_no, source_accessions, "
    "filed_at, quality_flags, calc_version) values (%s, %s, 'Q', 2026, %s, %s, %s, %s, 'USD', "
    "'reported', 'Revenues', %s, %s, %s, %s, 'test')"
)


def _rows(conn: psycopg.Connection[Any], cik: int, filed: date) -> None:
    for quarter, end, value, flags in (
        ("Q1", date(2026, 3, 31), 100, []),
        ("Q2", date(2026, 6, 30), 700, ["outlier_jump"]),
    ):
        accession = f"{cik:010d}-26-{end.month:06d}"
        conn.execute(
            INSERT,
            [
                cik,
                "revenue",
                quarter,
                end - timedelta(days=90),
                end,
                value,
                accession,
                [accession],
                filed,
                flags,
            ],
        )
    conn.execute(
        INSERT,
        [
            cik,
            "capex",
            "Q2",
            date(2026, 4, 1),
            date(2026, 6, 30),
            None,
            None,
            [],
            None,
            ["concept_mismatch"],
        ],
    )


@pytest.fixture
def stored(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Two companies' rows, committed (the CLI opens its own connection): one filed today,
    one in 2025. Removed afterwards."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    today = datetime.now(UTC).date()
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        for cik, name, ticker in ((FRESH, "Fresh Co", "FRSH"), (OLD, "Old Co", "OLDC")):
            conn.execute(
                "insert into companies (cik, name, filer_type, in_universe) "
                "values (%s, %s, 'domestic', true)",
                [cik, name],
            )
            conn.execute(
                "insert into securities (cik, ticker, valid_from) values (%s, %s, '2026-01-01')",
                [cik, ticker],
            )
        _rows(conn, FRESH, today)
        _rows(conn, OLD, date(2025, 11, 1))
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        for cik in (FRESH, OLD):
            conn.execute("delete from financial_metrics where cik = %s", [cik])
            conn.execute("delete from securities where cik = %s", [cik])
            conn.execute("delete from companies where cik = %s", [cik])


def test_the_report_lists_flags_by_company_and_metric(stored: str) -> None:
    result = CliRunner().invoke(cli.app, ["quality", "report"])

    assert result.exit_code == 0, result.output
    text = result.stdout
    assert "6 metric rows of 2 companies, 4 with a flag" in text
    assert "outlier_jump                        2" in text
    stale = text.split("Stale data")[1].split("By company")[0]
    assert "OLDC" in stale and "latest filing 2025-11-01" in stale and "FRSH" not in stale
    company = text.split("FRSH    900001")[1]
    assert "capex                     concept_mismatch 1 (2026-06-30)" in company
    assert "revenue                   outlier_jump 1 (2026-06-30)" in company
    assert text.rstrip().endswith("Information from public filings. Not investment advice.")


def test_the_report_takes_tickers_and_flags(stored: str) -> None:
    result = CliRunner().invoke(
        cli.app, ["quality", "report", "--ticker", "oldc", "--flag", "concept_mismatch"]
    )

    assert result.exit_code == 0, result.output
    assert "3 metric rows of 1 companies" in result.stdout
    assert "outlier_jump" not in result.stdout
    assert "concept_mismatch" in result.stdout


def test_an_unknown_flag_is_refused(stored: str) -> None:
    result = CliRunner().invoke(cli.app, ["quality", "report", "--flag", "bogus"])

    assert result.exit_code == 2
    assert "unknown flag bogus" in result.output


async def test_a_stale_companys_newest_value_says_so(conn: Conn) -> None:
    await conn.execute(
        "insert into companies (cik, name, filer_type) values (%s, 'Old Co', 'domestic')", [OLD]
    )
    for quarter, end, value in (("Q1", date(2026, 3, 31), 100), ("Q2", date(2026, 6, 30), 120)):
        accession = f"{OLD:010d}-26-{end.month:06d}"
        await conn.execute(
            INSERT,
            [
                OLD,
                "revenue",
                quarter,
                end - timedelta(days=90),
                end,
                value,
                accession,
                [accession],
                date(2026, 8, 1),
                [],
            ],
        )

    fresh = await metric_series(conn, OLD, "revenue", "Q", today=date(2026, 11, 29))
    stale = await metric_series(conn, OLD, "revenue", "Q", today=date(2026, 11, 30))

    assert [row.quality_flags for row in fresh] == [(), ()]
    assert [row.quality_flags for row in stale] == [("stale_data",), ()]
    assert stale[0].value == Decimal(120)  # a flag never changes or hides a value
