import pytest
from typer.testing import CliRunner

from marketdata.cli import app

GROUPS = [
    "sec",
    "ingest",
    "universe",
    "tasks",
    "backfill",
    "extract",
    "stats",
    "normalize",
    "quality",
    "clients",
    "keys",
    "webhooks",
    "usage",
]


def test_help_lists_command_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for group in GROUPS:
        assert group in result.output


def test_group_help_works_without_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = CliRunner().invoke(app, ["extract", "--help"])

    assert result.exit_code == 0
