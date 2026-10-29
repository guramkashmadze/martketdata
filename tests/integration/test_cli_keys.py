"""E5a-S1: `marketdata clients create|list` and `marketdata keys create|list|revoke`. The key is
printed once and stored as its sha256. The CLI commits its rows, so the test removes them."""

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli

KEY = re.compile(r"^    (mdk_[A-Za-z0-9_-]{43})$", re.MULTILINE)


@pytest.fixture
def database(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from api_requests")
        conn.execute("delete from api_keys")
        conn.execute("delete from clients")


def _run(*args: str) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_clients_and_keys(database: str) -> None:
    assert _run("clients", "create", "Demo Bank").startswith("Created client ")
    assert "exists already; nothing changed" in _run("clients", "create", "Demo Bank")

    created = _run("keys", "create", "Demo Bank", "--rate-per-min", "60")
    (key,) = KEY.findall(created)
    assert "shown only once" in created
    with psycopg.connect(database) as conn:
        stored = conn.execute("select key_hash, scope, rate_per_min from api_keys").fetchall()
    assert stored == [(hashlib.sha256(key.encode()).hexdigest(), "api", 60)]

    listed = _run("keys", "list", "--client", "Demo Bank")
    assert key not in listed
    assert re.search(r"Demo Bank\s+api\s+60\s+\S+ \S+\s+—\s+never", listed)
    assert re.search(r"Demo Bank\s+\S+ \S+\s+1$", _run("clients", "list"), re.MULTILINE)

    key_id = int(re.findall(r"^Key (\d+) ", created)[0])
    assert f"Key {key_id} of client Demo Bank is revoked" in _run("keys", "revoke", str(key_id))
    assert re.search(r"Demo Bank\s+\S+ \S+\s+0$", _run("clients", "list"), re.MULTILINE)


def test_a_key_for_an_unknown_client_is_refused(database: str) -> None:
    result = CliRunner().invoke(cli.app, ["keys", "create", "Nobody"])

    assert result.exit_code == 1
    assert "No client is named 'Nobody'" in result.output


def test_revoking_an_unknown_key_is_refused(database: str) -> None:
    result = CliRunner().invoke(cli.app, ["keys", "revoke", "999999"])

    assert result.exit_code == 1
    assert "No API key has id 999999" in result.output
