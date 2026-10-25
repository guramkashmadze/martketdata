"""Integration tests run against PostgreSQL at TEST_DATABASE_URL.

The database name must end in _test: the session starts by dropping and recreating its schema
with Alembic. The database is created if it is missing. When PostgreSQL is unreachable the
tests fail rather than skip, so a green run means they ran.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic_settings import BaseSettings, SettingsConfigDict

from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.ingest.security_master import LoadReport, SecurityMasterLoader
from marketdata.normalize.universe import SecurityOverride, parse_universe
from marketdata.sec import urls
from marketdata.sec.client import SecClient, TokenBucket
from marketdata.sec.raw_store import LocalRawStore

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


# --- E2: a fake SEC serving the committed fixtures, and a load through the test's connection.

UA = "marketdata-test ops@example.com"
SEC_FIXTURES = ROOT / "tests" / "fixtures" / "sec"
# Tickers of the test universe, with the CIK whose submissions fixture they need.
TEST_UNIVERSE = {
    "AAPL": 320193,
    "GOOGL": 1652044,
    "TGT": 27419,
    "SHOP": 1594805,
    "SPCX": 1181412,
    "GLD": 1222333,
    "SPY": 884394,
    "VOO": 36405,
    "TSM": 1046179,
    "BABA": 1577552,
}
TEST_UNIVERSE_CSV = "ticker,note\n" + "".join(f"{ticker},test\n" for ticker in TEST_UNIVERSE)
AS_OF = date(2026, 10, 25)


def fixture_routes() -> dict[str, bytes | int]:
    """SEC URL to the bytes a fixture holds; tests may replace an entry with a status code."""
    routes: dict[str, bytes | int] = {
        urls.COMPANY_TICKERS_EXCHANGE: (
            SEC_FIXTURES / "tickers/company_tickers_exchange.json"
        ).read_bytes(),
        urls.COMPANY_TICKERS_MF: (SEC_FIXTURES / "tickers/company_tickers_mf.json").read_bytes(),
    }
    for ticker, cik in TEST_UNIVERSE.items():
        routes[urls.submissions(cik)] = (SEC_FIXTURES / f"submissions/{ticker}.json").read_bytes()
    return routes


def sec_serving(routes: Mapping[str, bytes | int]) -> SecClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = routes.get(str(request.url), 404)
        if isinstance(body, int):
            return httpx2.Response(body)
        return httpx2.Response(200, content=body, headers={"Content-Type": "application/json"})

    return SecClient(UA, bucket=TokenBucket(1e6), transport=httpx2.MockTransport(handler))


def connect_to(conn: psycopg.AsyncConnection[Any]) -> Connect:
    """Hand out the test's connection; the test rolls its transaction back afterwards."""

    @asynccontextmanager
    async def connect() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        yield conn

    return connect


LoadMaster = Callable[..., Awaitable[LoadReport]]


@pytest.fixture
def load_master(conn: psycopg.AsyncConnection[Any], tmp_path: Path) -> LoadMaster:
    """Run a security-master load against fixtures, inside the test's transaction."""
    store = LocalRawStore(tmp_path / "raw")

    async def load(
        routes: Mapping[str, bytes | int] | None = None,
        universe: str = TEST_UNIVERSE_CSV,
        overrides: Sequence[SecurityOverride] = (),
        as_of: date = AS_OF,
        force: bool = False,
    ) -> LoadReport:
        async with sec_serving(routes if routes is not None else fixture_routes()) as sec:
            loader = SecurityMasterLoader(
                RawFetcher(sec, store, connect_to(conn)), connect_to(conn)
            )
            return await loader.load(parse_universe(universe), overrides, as_of=as_of, force=force)

    return load
