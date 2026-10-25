"""Settings from environment variables and .env (PLAN.md §11).

Each epic adds the settings it reads. Variables from later epics may already be in .env;
they are ignored until then. An empty value (`NAME=`) counts as not set.
"""

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, HttpUrl, PostgresDsn, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "live", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# The SEC asks for "Sample Company Name AdminContact@<sample company domain>.com".
_SEC_USER_AGENT = re.compile(r"^\S.*\s[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
SEC_USER_AGENT_FORMAT = "'<app name> <contact email>', for example 'marketdata ops@example.com'"


class SettingsError(Exception):
    """Configuration is missing or invalid. The message says which variable and how to fix it."""


def check_sec_user_agent(value: str) -> str:
    value = value.strip()
    if not _SEC_USER_AGENT.match(value):
        raise ValueError(f"must be {SEC_USER_AGENT_FORMAT}")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    env: Env = "dev"
    log_level: LogLevel = "INFO"
    database_url: PostgresDsn

    # E1. Only processes that call the SEC need the User-Agent; they check it at startup with
    # require_sec_user_agent (D-043). A value that is set is always validated.
    sec_user_agent: Annotated[str, AfterValidator(check_sec_user_agent)] | None = None
    # Guardrail 6: at most 8 requests per second; the SEC allows about 10.
    sec_max_rps: float = Field(default=8.0, gt=0, le=8)

    # E1 raw store. With s3, S3_BUCKET is required (checked by create_raw_store). Without an
    # endpoint URL boto3 talks to AWS; R2 and B2 need S3_ENDPOINT_URL, and B2 its S3_REGION.
    raw_store_kind: Literal["local", "s3"] = "local"
    raw_store_path: Path = Path("var/raw")
    s3_endpoint_url: HttpUrl | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None

    # E2. Hand-maintained files, relative to the working directory (PLAN.md §9.1, D-051).
    universe_file: Path = Path("data/universe.csv")
    security_overrides_file: Path = Path("data/security_overrides.csv")


def load_settings(env_file: str | None = ".env") -> Settings:
    """Load settings, failing fast with one line per missing or invalid variable."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise SettingsError(_describe(exc)) from None


def require_sec_user_agent(settings: Settings) -> str:
    """The User-Agent for SEC requests. Processes that call the SEC refuse to start without it."""
    if settings.sec_user_agent is None:
        raise SettingsError(
            _message([f"  - SEC_USER_AGENT is missing. Set it to {SEC_USER_AGENT_FORMAT}."])
        )
    return settings.sec_user_agent


def _describe(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        name = ".".join(str(part) for part in error["loc"]).upper()
        if error["type"] == "missing":
            lines.append(f"  - {name} is missing.")
        else:
            lines.append(f"  - {name} is invalid: {error['msg']}.")
    return _message(lines)


def _message(lines: list[str]) -> str:
    return (
        "Invalid configuration:\n"
        + "\n".join(lines)
        + "\nSet it in the environment or in .env (see .env.example and docs/PLAN.md §11)."
    )
