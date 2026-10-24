from pathlib import Path

import pytest

from marketdata.config import SettingsError, load_settings

URL = "postgresql://marketdata:marketdata@localhost:5432/marketdata"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENV", "LOG_LEVEL", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_loads_from_environment_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)

    settings = load_settings(env_file=None)

    assert str(settings.database_url) == URL
    assert settings.env == "dev"
    assert settings.log_level == "INFO"


def test_loads_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"ENV=prod\nLOG_LEVEL=WARNING\nDATABASE_URL={URL}\nSEC_USER_AGENT=later\n")

    settings = load_settings(env_file=str(env_file))

    assert settings.env == "prod"
    assert settings.log_level == "WARNING"


def test_missing_required_value_fails_with_clear_message() -> None:
    with pytest.raises(SettingsError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "DATABASE_URL is missing" in message
    assert ".env.example" in message


def test_invalid_value_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ENV", "staging")

    with pytest.raises(SettingsError, match="ENV is invalid"):
        load_settings(env_file=None)


def test_database_url_must_be_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://localhost/marketdata")

    with pytest.raises(SettingsError, match="DATABASE_URL is invalid"):
        load_settings(env_file=None)
