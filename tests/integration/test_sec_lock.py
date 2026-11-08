"""E3-S2: one process calls the SEC at a time (D-007, D-044); a worker started with --wait, as in
Docker Compose, waits for the slot and stops cleanly meanwhile (D-137)."""

import asyncio
import os
import re
import signal
import socket
from pathlib import Path

import psycopg
import pytest
from conftest import sec_serving
from structlog.testing import capture_logs
from typer.testing import CliRunner

from marketdata import cli
from marketdata.config import Settings
from marketdata.db.repositories.locks import SEC_SLOT
from marketdata.ingest.sec_lock import SecProcessBusy, SecWaitStopped, sec_process_lock
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks.handlers import build_registry
from marketdata.tasks.worker import process_name, serve

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


# --- D-137: the worker in Docker Compose waits for the slot rather than refusing to start.


async def test_a_process_that_waits_takes_the_slot_when_its_holder_stops(
    migrated_database: str,
) -> None:
    holder = await psycopg.AsyncConnection.connect(
        migrated_database, autocommit=True, application_name="marketdata worker on laptop"
    )
    await holder.execute("select pg_advisory_lock(%s, %s)", SEC_SLOT)
    entered = asyncio.Event()

    async def wait_for_the_slot() -> None:
        async with sec_process_lock(
            migrated_database, "marketdata worker on docker-compose", wait=True, retry_every=0.05
        ):
            entered.set()

    with capture_logs() as logs:
        waiting = asyncio.create_task(wait_for_the_slot())
        await asyncio.sleep(0.3)
        assert not entered.is_set()  # still waiting, without another warning each try
        await holder.close()  # the slot goes with its holder's connection
        await asyncio.wait_for(waiting, 5)

    assert entered.is_set()
    busy = [entry for entry in logs if entry["event"] == "sec_slot_busy"]
    assert len(busy) == 1, busy
    assert busy[0]["holder"].startswith("'marketdata worker on laptop' (PostgreSQL pid ")
    assert [entry["event"] for entry in logs][-1] == "sec_slot_taken"


async def test_a_worker_waiting_for_the_slot_stops_cleanly_on_sigterm(
    migrated_database: str, tmp_path: Path
) -> None:
    """`docker compose stop worker` while a worker started by hand holds the slot."""
    holder = await psycopg.AsyncConnection.connect(migrated_database, autocommit=True)
    await holder.execute("select pg_advisory_lock(%s, %s)", SEC_SLOT)
    settings = Settings(_env_file=None, database_url=migrated_database)  # type: ignore[call-arg]
    try:
        running = asyncio.create_task(
            serve(
                settings,
                build_registry(),
                sec_serving({}),
                LocalRawStore(tmp_path / "raw"),
                concurrency=1,
                wait=True,
            )
        )
        await asyncio.sleep(0.3)  # waiting: the next try is 30 seconds away
        assert not running.done()
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(running, 5)  # ends at once, without an error
    finally:
        await holder.close()


async def test_a_stop_while_waiting_leaves_without_the_slot(migrated_database: str) -> None:
    holder = await psycopg.AsyncConnection.connect(migrated_database, autocommit=True)
    await holder.execute("select pg_advisory_lock(%s, %s)", SEC_SLOT)
    stop = asyncio.Event()

    async def wait_for_the_slot() -> None:
        async with sec_process_lock(migrated_database, "marketdata worker", wait=True, stop=stop):
            pytest.fail("took a slot that another process holds")

    try:
        waiting = asyncio.create_task(wait_for_the_slot())
        await asyncio.sleep(0.1)
        stop.set()
        with pytest.raises(SecWaitStopped):
            await asyncio.wait_for(waiting, 5)
    finally:
        await holder.close()


def test_the_worker_names_itself_with_its_host() -> None:
    assert process_name() == f"marketdata worker on {socket.gethostname()}"
