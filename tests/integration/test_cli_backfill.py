"""E3-S5: `marketdata backfill filings --years N` queues one backfill per universe company; the
worker runs them. The CLI commits, so the test removes what it wrote."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli
from marketdata.tasks.kinds import FILINGS_BACKFILL
from marketdata.tasks.scheduler import years_before

CIKS = (320193, 1652044)


@pytest.fixture
def universe(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        for cik in CIKS:
            conn.execute(
                "insert into companies (cik, name, filer_type, in_universe) "
                "values (%s, 'test', 'domestic', true)",
                [cik],
            )
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from tasks where kind = %s", [FILINGS_BACKFILL])
        conn.execute("delete from companies where cik = any(%s)", [list(CIKS)])


def test_one_backfill_per_universe_company(universe: str) -> None:
    since = years_before(datetime.now(UTC).date(), 5)

    first = CliRunner().invoke(cli.app, ["backfill", "filings", "--years", "5"])
    again = CliRunner().invoke(cli.app, ["backfill", "filings", "--years", "3"])

    assert first.exit_code == 0, first.output
    assert f"Queued 2 filing backfills back to {since} for 2 universe companies" in first.stdout
    assert "2 were already pending and keep their own start date." in again.stdout
    with psycopg.connect(universe) as conn:
        rows = conn.execute(
            "select key, payload from tasks where kind = %s order by key", [FILINGS_BACKFILL]
        ).fetchall()
    assert rows == [
        (str(cik), {"cik": cik, "since": since.isoformat()}) for cik in sorted(CIKS, key=str)
    ]


def test_an_empty_universe_is_an_error(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", migrated_database)

    result = CliRunner().invoke(cli.app, ["backfill", "filings"])

    assert result.exit_code == 1
    assert "No universe companies" in result.output
