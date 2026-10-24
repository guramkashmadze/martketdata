"""E1-S3: `marketdata sec get` refuses to start without the settings it needs."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from marketdata.cli import app

DB = "postgresql://marketdata:marketdata@127.0.0.1:1/marketdata"
URL = "https://data.sec.gov/submissions/CIK0000320193.json"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No .env here, so only the variables a test sets are seen.
    monkeypatch.chdir(tmp_path)
    for name in ("SEC_USER_AGENT", "RAW_STORE_KIND", "S3_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", DB)


def test_sec_get_fails_at_startup_without_user_agent() -> None:
    result = CliRunner().invoke(app, ["sec", "get", URL])

    assert result.exit_code == 2
    assert "SEC_USER_AGENT is missing" in result.output


def test_sec_get_fails_at_startup_without_s3_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "marketdata-test ops@example.com")
    monkeypatch.setenv("RAW_STORE_KIND", "s3")

    result = CliRunner().invoke(app, ["sec", "get", URL])

    assert result.exit_code == 2
    assert "S3_BUCKET is missing" in result.output


def test_sec_get_help_works_without_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    result = CliRunner().invoke(app, ["sec", "get", "--help"])

    assert result.exit_code == 0
    assert "--key" in result.output
