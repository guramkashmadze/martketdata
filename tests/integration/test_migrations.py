from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

E0_TABLES = {"tasks", "clients", "api_keys", "api_requests"}
E1_TABLES = {"raw_objects"}
E2_TABLES = {"companies", "securities"}


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def test_head_is_0003(alembic_config: Config) -> None:
    assert ScriptDirectory.from_config(alembic_config).get_current_head() == "0003"


def test_upgrade_and_downgrade(alembic_config: Config, migrated_database: str) -> None:
    assert _tables(migrated_database) >= E0_TABLES | E1_TABLES | E2_TABLES

    command.downgrade(alembic_config, "-1")
    assert _tables(migrated_database) & E2_TABLES == set()
    assert _tables(migrated_database) >= E0_TABLES | E1_TABLES

    command.downgrade(alembic_config, "-1")
    assert _tables(migrated_database) & E1_TABLES == set()
    assert _tables(migrated_database) >= E0_TABLES

    command.downgrade(alembic_config, "-1")
    assert _tables(migrated_database) & E0_TABLES == set()

    command.upgrade(alembic_config, "head")
    assert _tables(migrated_database) >= E0_TABLES | E1_TABLES | E2_TABLES


async def test_task_status_is_checked(conn: psycopg.AsyncConnection[Any]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute("insert into tasks (kind, key, status) values ('k', '1', 'bogus')")


async def test_api_key_defaults(conn: psycopg.AsyncConnection[Any]) -> None:
    cur = await conn.execute("""
        with client as (insert into clients (name) values ('operator test') returning client_id)
        insert into api_keys (client_id, key_hash) select client_id, 'sha256-of-key' from client
        returning scope, rate_per_min, revoked_at
        """)
    assert await cur.fetchone() == ("api", 120, None)
