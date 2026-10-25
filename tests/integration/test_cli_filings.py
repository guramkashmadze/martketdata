"""E3 demo: `marketdata filings list <TICKER>` shows a company's filings with event labels.
The CLI reads committed rows, so the test writes its own and removes them afterwards."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli

FUBO = 1484769


@pytest.fixture
def fubo(migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) "
            "values (%s, 'FuboTV Inc.', 'domestic', true)",
            [FUBO],
        )
        conn.execute(
            "insert into securities (cik, ticker, valid_from) values (%s, 'FUBO', '2026-10-25')",
            [FUBO],
        )
        conn.execute(
            "insert into filings (accession_no, cik, form, filed_at, items_8k, file_number) values "
            "('0000950157-25-000917', %(cik)s, 'SCHEDULE 13D', '2025-11-05', null, '005-89078'), "
            "('0001493152-25-020163', %(cik)s, '8-K', '2025-10-30', '{5.01,9.01}', '001-39590')",
            {"cik": FUBO},
        )
    yield
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from filings where cik = %s", [FUBO])
        conn.execute("delete from securities where cik = %s", [FUBO])
        conn.execute("delete from companies where cik = %s", [FUBO])


def test_filings_list_shows_event_labels(fubo: None) -> None:
    result = CliRunner().invoke(cli.app, ["filings", "list", "fubo", "--limit", "10"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "FuboTV Inc. (FUBO, CIK 1484769): the 2 latest filings",
        "  2025-11-05  SCHEDULE 13D     0000950157-25-000917  "
        "An investor crossed 5% and may seek to influence the company [high]",
        "  2025-10-30  8-K              0001493152-25-020163  "
        "Change in control of the company [high]",
        "Information from public filings. Not investment advice.",
    ]


def test_an_unknown_ticker_is_an_error(fubo: None) -> None:
    result = CliRunner().invoke(cli.app, ["filings", "list", "NOPE"])

    assert result.exit_code == 1
