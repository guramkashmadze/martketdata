"""Settings from environment variables and .env (PLAN.md §11).

Each epic adds the settings it reads. Variables from later epics may already be in .env;
they are ignored until then.
"""

from typing import Literal

from pydantic import PostgresDsn, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "live", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class SettingsError(Exception):
    """Configuration is missing or invalid. The message says which variable and how to fix it."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Env = "dev"
    log_level: LogLevel = "INFO"
    database_url: PostgresDsn


def load_settings(env_file: str | None = ".env") -> Settings:
    """Load settings, failing fast with one line per missing or invalid variable."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise SettingsError(_describe(exc)) from None


def _describe(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        name = ".".join(str(part) for part in error["loc"]).upper()
        if error["type"] == "missing":
            lines.append(f"  - {name} is missing.")
        else:
            lines.append(f"  - {name} is invalid: {error['msg']}.")
    return (
        "Invalid configuration:\n"
        + "\n".join(lines)
        + "\nSet it in the environment or in .env (see .env.example and docs/PLAN.md §11)."
    )
