from pathlib import Path

import pytest

from marketdata.config import SettingsError, load_settings

URL = "postgresql://marketdata:marketdata@localhost:5432/marketdata"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENV", "LOG_LEVEL", "DATABASE_URL", "SEC_USER_AGENT", "SEC_MAX_RPS"):
        monkeypatch.delenv(name, raising=False)


def test_loads_from_environment_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)

    settings = load_settings(env_file=None)

    assert str(settings.database_url) == URL
    assert settings.env == "dev"
    assert settings.log_level == "INFO"


def test_loads_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"ENV=prod\nLOG_LEVEL=WARNING\nDATABASE_URL={URL}\nUNIVERSE_FILE=later\n")

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


def test_sec_settings_default_to_unset_user_agent_and_8_rps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)

    settings = load_settings(env_file=None)

    assert settings.sec_user_agent is None
    assert settings.sec_max_rps == 8.0


def test_empty_values_count_as_not_set(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={URL}\nSEC_USER_AGENT=\nSEC_MAX_RPS=\n")

    settings = load_settings(env_file=str(env_file))

    assert settings.sec_user_agent is None
    assert settings.sec_max_rps == 8.0


def test_user_agent_is_validated_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("SEC_USER_AGENT", "marketdata")

    with pytest.raises(SettingsError, match="SEC_USER_AGENT is invalid.*contact email"):
        load_settings(env_file=None)


def test_user_agent_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("SEC_USER_AGENT", " marketdata ops@example.com ")

    assert load_settings(env_file=None).sec_user_agent == "marketdata ops@example.com"


@pytest.mark.parametrize("rps", ["0", "-1", "8.5", "10"])
def test_sec_max_rps_stays_within_fair_access(monkeypatch: pytest.MonkeyPatch, rps: str) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("SEC_MAX_RPS", rps)

    with pytest.raises(SettingsError, match="SEC_MAX_RPS is invalid"):
        load_settings(env_file=None)
