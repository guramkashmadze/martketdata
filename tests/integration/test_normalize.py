"""E4b-S5: `marketdata normalize --all` rebuilds financial_metrics from the stored facts with no
SEC request (it needs no SEC settings), prints its duration, writes the current calc version
and logs each value it changes (D-027)."""

import asyncio
import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from conftest import SEC_FIXTURES
from typer.testing import CliRunner

from marketdata import cli
from marketdata.db.repositories import xbrl_facts
from marketdata.normalize.companyfacts import parse_companyfacts
from marketdata.normalize.metrics import CALC_VERSION

AAPL = 320193
FACTS = parse_companyfacts((SEC_FIXTURES / "companyfacts" / "AAPL-20261028.json").read_bytes())


@pytest.fixture
def stored_facts(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Apple's facts and one metric row an earlier calc version stored with another value,
    committed (the CLI opens its own connection). Removed afterwards."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    async def load() -> None:
        async with await psycopg.AsyncConnection.connect(
            migrated_database, autocommit=True
        ) as conn:
            await conn.execute(
                "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, "
                "in_universe) values (%s, 'Apple Inc.', '3571', '0926', 'domestic', true)",
                [AAPL],
            )
            await conn.execute(
                "insert into securities (cik, ticker, valid_from) "
                "values (%s, 'AAPL', '2026-01-01')",
                [AAPL],
            )
            await xbrl_facts.upsert(conn, FACTS.facts)
            await conn.execute(
                "insert into financial_metrics (cik, metric, period_type, fiscal_year, "
                "fiscal_period, period_start, period_end, value, unit, method, concept, "
                "accession_no, source_accessions, filed_at, calc_version) values (%s, 'revenue', "
                "'Q', 2026, 'Q3', '2026-03-29', '2026-06-27', 1, 'USD', 'reported', "
                "'RevenueFromContractWithCustomerExcludingAssessedTax', '0000320193-26-000020', "
                "'{0000320193-26-000020}', '2026-07-31', '0.9')",
                [AAPL],
            )

    asyncio.run(load())
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        # E5b: each value change is announced (data.changed) with a task that publishes it.
        conn.execute("delete from tasks where kind = 'webhook_publish'")
        conn.execute("delete from webhook_events where type = 'data.changed'")
        conn.execute("delete from value_changes where cik = %s", [AAPL])
        conn.execute("delete from financial_metrics where cik = %s", [AAPL])
        conn.execute("delete from xbrl_facts where cik = %s", [AAPL])
        conn.execute("delete from securities where cik = %s", [AAPL])
        conn.execute("delete from companies where cik = %s", [AAPL])


def test_normalize_all_rebuilds_from_stored_facts(stored_facts: str) -> None:
    result = CliRunner().invoke(cli.app, ["normalize", "--all"])

    assert result.exit_code == 0, result.output
    rebuilt, checked = result.stdout.strip().splitlines()
    assert re.fullmatch(
        rf"Rebuilt the metrics of 1 companies from stored facts in \d+\.\d s, calc version "
        rf"{re.escape(CALC_VERSION)}: [\d,]+ rows, [\d,]+ written, 1 value changes logged\.",
        rebuilt,
    ), result.stdout
    # No earnings releases are stored here; E4b-S6's tests check releases.
    assert checked.startswith("Cross-checked 0 earnings releases against their 10-Qs and 10-Ks")
    with psycopg.connect(stored_facts) as conn:
        versions = conn.execute(
            "select distinct calc_version from financial_metrics where cik = %s", [AAPL]
        ).fetchall()
        [change] = conn.execute(
            "select reason, detail, old_value, new_value, change_id from value_changes "
            "where cik = %s",
            [AAPL],
        ).fetchall()
        announced = conn.execute(
            "select subject, livemode from webhook_events where type = 'data.changed'"
        ).fetchall()
    assert versions == [(CALC_VERSION,)]
    assert announced == [(str(change[4]), True)]  # E5b: the change goes out as data.changed
    assert change[:2] == ("correction", f"calc version 0.9 to {CALC_VERSION}")
    assert (change[2], change[3]) == (1, 109417000000)

    again = CliRunner().invoke(cli.app, ["normalize", "--ticker", "aapl"])
    assert again.exit_code == 0, again.output
    assert " 0 written, 0 value changes logged." in again.stdout


def test_normalize_needs_all_or_a_ticker(stored_facts: str) -> None:
    result = CliRunner().invoke(cli.app, ["normalize"])

    assert result.exit_code == 2
    assert "Say --all, or name companies with --ticker." in result.output
