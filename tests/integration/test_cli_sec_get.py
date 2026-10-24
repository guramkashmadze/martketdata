"""E1 demo: `marketdata sec get <url>` fetches through the client, stores the file and prints
its raw key. The SEC is replaced by a mocked transport; the database is the test database."""

from pathlib import Path

import httpx2
import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli
from marketdata.config import Settings
from marketdata.sec import client as sec_client
from marketdata.sec.client import SecClient, TokenBucket

UA = "marketdata-test ops@example.com"
URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/index.json"
KEY = "raw/sec/320193/0000320193-24-000123/index.json"
BODY = b'{"directory": {"name": "/Archives/edgar/data/320193/000032019324000123"}}'


@pytest.fixture
def sec_mocked(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Request]:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=BODY, headers={"Content-Type": "application/json"})

    def from_settings(settings: Settings) -> SecClient:
        assert settings.sec_user_agent == UA
        return SecClient(UA, bucket=TokenBucket(8.0), transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(sec_client.SecClient, "from_settings", from_settings)
    return requests


def test_sec_get_stores_the_file_and_prints_its_key(
    migrated_database: str,
    sec_mocked: list[httpx2.Request],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setenv("RAW_STORE_KIND", "local")
    monkeypatch.setenv("RAW_STORE_PATH", str(tmp_path / "raw"))

    try:
        result = CliRunner().invoke(cli.app, ["sec", "get", URL])

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == KEY
        assert len(sec_mocked) == 1
        assert (tmp_path / "raw" / KEY).read_bytes() == BODY
        with psycopg.connect(migrated_database) as conn:
            row = conn.execute(
                "select url, size_bytes from raw_objects where key = %s", [KEY]
            ).fetchone()
        assert row == (URL, len(BODY))
    finally:
        # The CLI commits; remove its row so other tests start clean.
        with psycopg.connect(migrated_database) as conn:
            conn.execute("delete from raw_objects where key = %s", [KEY])


def test_sec_get_refuses_urls_without_a_key_layout(
    migrated_database: str,
    sec_mocked: list[httpx2.Request],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setenv("RAW_STORE_PATH", str(tmp_path / "raw"))

    result = CliRunner().invoke(
        cli.app,
        ["sec", "get", "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"],
    )

    assert result.exit_code == 2
    assert "streaming" in result.output
    assert sec_mocked == []
