"""E3-S2: one process calls the SEC at a time (D-007, D-044)."""

import re
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli
from marketdata.db.repositories.locks import SEC_SLOT
from marketdata.ingest.sec_lock import SecProcessBusy, sec_process_lock

UA = "marketdata-test ops@example.com"
ROOT = Path(__file__).resolve().parents[2]


async def test_a_second_sec_process_is_refused_and_told_who_holds_the_slot(
    migrated_database: str,
) -> None:
    async with sec_process_lock(migrated_database, "marketdata worker"):
        with pytest.raises(SecProcessBusy) as refused:
            async with sec_process_lock(migrated_database, "marketdata sec get"):
                pytest.fail("two processes held the SEC slot")

    message = str(refused.value)
    assert re.search(
        r"Another process is calling the SEC: 'marketdata worker' \(PostgreSQL pid \d+, "
        r"connected \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\)\.",
        message,
    ), message
    # The slot goes with the holder's connection.
    async with sec_process_lock(migrated_database, "marketdata sec get"):
        pass


def test_cli_commands_that_call_the_sec_refuse_while_the_slot_is_held(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setenv("RAW_STORE_PATH", str(tmp_path / "raw"))
    monkeypatch.setenv("UNIVERSE_FILE", str(ROOT / "data" / "universe.csv"))
    monkeypatch.setenv("SECURITY_OVERRIDES_FILE", str(ROOT / "data" / "security_overrides.csv"))
    monkeypatch.setenv("PREDECESSORS_FILE", str(ROOT / "data" / "predecessors.csv"))
    url = "https://data.sec.gov/submissions/CIK0000320193.json"

    with psycopg.connect(
        migrated_database, autocommit=True, application_name="marketdata worker"
    ) as holder:
        holder.execute("select pg_advisory_lock(%s, %s)", SEC_SLOT)
        sec_get = CliRunner().invoke(cli.app, ["sec", "get", url])
        ingest = CliRunner().invoke(cli.app, ["ingest", "security-master"])
        worker = CliRunner().invoke(cli.app, ["worker"])

    for result in (sec_get, ingest, worker):
        assert result.exit_code == 1, result.output
        assert "Another process is calling the SEC: 'marketdata worker'" in result.output
