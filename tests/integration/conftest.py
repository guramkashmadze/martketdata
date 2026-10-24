"""Integration tests run against PostgreSQL at TEST_DATABASE_URL.

The database name must end in _test: the session starts by dropping and recreating its schema
with Alembic. The database is created if it is missing. When PostgreSQL is unreachable the
tests fail rather than skip, so a green run means they ran.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


class IntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    test_database_url: str = "postgresql://marketdata:marketdata@localhost:5432/marketdata_test"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(pytest.mark.integration)


def _ensure_database(url: str, dbname: str, where: str) -> None:
    try:
        psycopg.connect(url, connect_timeout=5).close()
        return
    except psycopg.OperationalError as exc:
        if exc.sqlstate != "3D000" and "does not exist" not in str(exc):
            pytest.fail(
                f"PostgreSQL at {where} is unreachable: {exc}\n"
                "Start it (see README) or point TEST_DATABASE_URL at a running server.",
                pytrace=False,
            )
    with psycopg.connect(url, dbname="postgres", autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))


@pytest.fixture(scope="session")
def database_url() -> str:
    url = IntegrationSettings().test_database_url
    info = psycopg.conninfo.conninfo_to_dict(url)
    dbname = str(info.get("dbname") or "")
    if not dbname.endswith("_test"):
        pytest.exit(
            f"TEST_DATABASE_URL must name a database ending in _test, got {dbname!r}. "
            "The tests drop and recreate its schema.",
            returncode=2,
        )
    where = f"{info.get('host', 'localhost')}:{info.get('port', 5432)}/{dbname}"
    _ensure_database(url, dbname, where)
    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def migrated_database(alembic_config: Config, database_url: str) -> str:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    return database_url


@pytest.fixture
async def conn(migrated_database: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A connection inside a transaction that is rolled back after the test."""
    async with await psycopg.AsyncConnection.connect(migrated_database) as connection:
        yield connection
        await connection.rollback()
