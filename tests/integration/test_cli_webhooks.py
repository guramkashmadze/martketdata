"""E5b-S1: `marketdata webhooks create|list|disable|enable|test`. The signing secret is printed
once and stored only encrypted with WEBHOOK_ENCRYPTION_KEY. The CLI commits its rows, so the
test removes them."""

import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from marketdata import cli
from marketdata.webhooks.signing import decrypt_secret

KEY = Fernet.generate_key().decode()
SECRET = re.compile(r"^    (whsec_[A-Za-z0-9_-]{43})$", re.MULTILINE)


@pytest.fixture
def database(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", KEY)
    monkeypatch.setenv("ENV", "live")
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from tasks where kind like 'webhook_%'")
        conn.execute("delete from webhook_deliveries")
        conn.execute("delete from webhook_events")
        conn.execute("delete from webhook_destinations")
        conn.execute("delete from api_requests")
        conn.execute("delete from api_keys")
        conn.execute("delete from clients")


def _run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == code, result.output
    return result.output


def test_destinations(database: str) -> None:
    _run("clients", "create", "Demo Bank")

    created = _run(
        "webhooks",
        "create",
        "Demo Bank",
        "https://hooks.example.com/marketdata",
        "--event",
        "data.changed",
        "--event",
        "earnings.verified",
    )

    (secret,) = SECRET.findall(created)
    assert created.startswith("Destination ") and "shown only once" in created
    assert "Events: earnings.verified, data.changed." in created
    with psycopg.connect(database) as conn:
        row = conn.execute("select dest_id, secret_enc, event_types from webhook_destinations")
        dest_id, secret_enc, types = row.fetchone() or (None, "", [])
    assert secret not in secret_enc and decrypt_secret(KEY, secret_enc) == secret
    assert types == ["earnings.verified", "data.changed"]

    listed = _run("webhooks", "list")
    assert f"{dest_id:>4}  Demo Bank  https://hooks.example.com/marketdata" in listed
    assert "on; failed deliveries in a row: 0" in listed and "latest attempt never" in listed
    assert secret not in listed and secret_enc not in listed

    off = _run("webhooks", "disable", str(dest_id))
    assert "switched off by the operator" in off
    assert "off since " in _run("webhooks", "list", "--client", "Demo Bank")
    assert "is on" in _run("webhooks", "enable", str(dest_id))

    # No release is stored, so there is nothing a test event could carry.
    nothing = _run("webhooks", "test", str(dest_id), "--wait", "0", code=1)
    assert "No data is stored yet for a test event of type earnings.verified" in nothing


def test_destinations_are_checked(database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _run("clients", "create", "Demo Bank")

    insecure = _run("webhooks", "create", "Demo Bank", "http://hooks.example.com/in", code=2)
    unknown = _run("webhooks", "create", "Nobody", "https://hooks.example.com/in", code=1)
    missing = _run("webhooks", "disable", "999999", code=1)
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY")
    no_key = _run("webhooks", "create", "Demo Bank", "https://hooks.example.com/in", code=2)

    assert "must use https" in insecure
    assert "No client is named 'Nobody'" in unknown
    assert "No webhook destination has id 999999" in missing
    assert "WEBHOOK_ENCRYPTION_KEY is missing" in no_key
    assert "No webhook destinations yet" in _run("webhooks", "list")
