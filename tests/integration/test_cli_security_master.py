"""E2-S1/S2: `marketdata ingest security-master` loads the security master and prints the
universe with a class per company; `marketdata universe check` reads it back without the SEC.

The SEC is a mock serving the committed fixtures; the CLI commits to the test database, so
the test removes what it wrote.
"""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from conftest import TEST_UNIVERSE_CSV, UA, fixture_routes, sec_serving
from typer.testing import CliRunner

from marketdata import cli
from marketdata.config import Settings
from marketdata.sec import client as sec_client
from marketdata.sec.client import SecClient

R1 = "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/R1.htm"


@pytest.fixture
def cli_env(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    def from_settings(settings: Settings) -> SecClient:
        assert settings.sec_user_agent == UA
        return sec_serving(fixture_routes())

    monkeypatch.setattr(sec_client.SecClient, "from_settings", from_settings)
    monkeypatch.chdir(tmp_path)  # no .env here
    (tmp_path / "universe.csv").write_text(TEST_UNIVERSE_CSV + "NOPE,not listed\n")
    (tmp_path / "overrides.csv").write_text(
        f"ticker,security_type,source_url\nGOOGM,preferred,{R1}\n"
    )
    for name, value in {
        "DATABASE_URL": migrated_database,
        "SEC_USER_AGENT": UA,
        "RAW_STORE_KIND": "local",
        "RAW_STORE_PATH": str(tmp_path / "raw"),
        "UNIVERSE_FILE": str(tmp_path / "universe.csv"),
        "SECURITY_OVERRIDES_FILE": str(tmp_path / "overrides.csv"),
    }.items():
        monkeypatch.setenv(name, value)
    yield tmp_path
    with psycopg.connect(migrated_database) as conn:
        conn.execute("delete from securities")
        conn.execute("delete from companies")
        conn.execute("delete from raw_objects where key like 'raw/sec/api/%'")


def test_ingest_prints_changes_and_the_universe_then_check_reads_it_back(cli_env: Path) -> None:
    runner = CliRunner()

    loaded = runner.invoke(cli.app, ["ingest", "security-master"])

    assert loaded.exit_code == 0, loaded.output
    out = loaded.stdout
    assert "51 exchange rows and 447 fund rows give 495 tickers." in out
    assert "  Skipped 'NONE.' (exchange list, CIK 1347123): not a ticker" in out
    assert "  SPCX is in both lists: kept CIK 1181412 from the exchange list" in out
    assert "Securities: 495 opened, 0 updated, 0 unchanged." in out
    assert "Companies: 32 added, 0 changed." in out
    assert f"Universe ({cli_env / 'universe.csv'}): 11 tickers, 10 companies." in out
    assert "GLD" in out and "SIC 6221 and no 8-K item 2.02" in out
    assert "  Unknown tickers (in no SEC list): NOPE" in out
    assert "  Classes: domestic 5, foreign_private_issuer 2, fund 3, other 0, unknown 0" in out

    again = runner.invoke(cli.app, ["ingest", "security-master"])
    assert again.exit_code == 0, again.output
    assert "Securities: 0 opened, 0 updated, 495 unchanged." in again.stdout
    assert "Companies: 0 added, 0 changed." in again.stdout

    checked = runner.invoke(cli.app, ["universe", "check"])
    assert checked.exit_code == 1  # NOPE is in no SEC list
    assert "  Classes: domestic 5, foreign_private_issuer 2, fund 3, other 0, unknown 0" in (
        checked.stdout
    )
    assert "to add" not in checked.stdout  # the load has marked every universe company


def test_a_universe_file_with_problems_stops_before_any_request(cli_env: Path) -> None:
    (cli_env / "universe.csv").write_text("ticker,note\nAAPL,\n")

    result = CliRunner().invoke(cli.app, ["ingest", "security-master"])

    assert result.exit_code == 2
    assert "line 2: AAPL has no note" in result.output


def test_an_override_needs_an_sec_filing_page(cli_env: Path) -> None:
    (cli_env / "overrides.csv").write_text(
        "ticker,security_type,source_url\nGOOGM,preferred,https://example.org/cover\n"
    )

    result = CliRunner().invoke(cli.app, ["ingest", "security-master"])

    assert result.exit_code == 2
    assert "GOOGM's source_url is not an SEC filing page" in result.output
