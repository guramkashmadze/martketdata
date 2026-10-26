from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

E0_TABLES = {"tasks", "clients", "api_keys", "api_requests"}
E1_TABLES = {"raw_objects"}
E2_TABLES = {"companies", "securities"}
E3_TABLES = {"filings", "watch_state"}
E7A_TABLES = {"documents", "company_predecessors"}
EARNINGS_TABLES = {"earnings_releases", "earnings_facts", "value_changes"}


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def _columns(url: str, table: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "select column_name from information_schema.columns where table_name = %s", [table]
        ).fetchall()
    return {row[0] for row in rows}


def test_head_is_0006(alembic_config: Config) -> None:
    assert ScriptDirectory.from_config(alembic_config).get_current_head() == "0006"


def test_upgrade_and_downgrade(alembic_config: Config, migrated_database: str) -> None:
    every = E0_TABLES | E1_TABLES | E2_TABLES | E3_TABLES | E7A_TABLES | EARNINGS_TABLES
    assert _tables(migrated_database) >= every
    assert "priority" in _columns(migrated_database, "tasks")

    command.downgrade(alembic_config, "-1")
    assert _tables(migrated_database) & EARNINGS_TABLES == set()
    assert _tables(migrated_database) >= E7A_TABLES

    command.downgrade(alembic_config, "-1")
    assert _tables(migrated_database) & E7A_TABLES == set()
    assert "priority" not in _columns(migrated_database, "tasks")
    assert _tables(migrated_database) >= E0_TABLES | E1_TABLES | E2_TABLES | E3_TABLES

    command.downgrade(alembic_config, "-1")
    assert _tables(migrated_database) & E3_TABLES == set()
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
    assert _tables(migrated_database) >= every


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


async def test_filings_are_keyed_by_accession_and_listing_company(
    conn: psycopg.AsyncConnection[Any],
) -> None:
    insert = (
        "insert into filings (accession_no, cik, form, filed_at) "
        "values ('0000034088-26-000093', %s, '10-Q', '2026-08-03')"
    )
    await conn.execute(insert, [34088])
    await conn.execute(insert, [2115436])  # a joint filing, listed under both registrants

    with pytest.raises(psycopg.errors.UniqueViolation):
        await conn.execute(insert, [34088])


async def test_filing_accession_numbers_are_checked(conn: psycopg.AsyncConnection[Any]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute(
            "insert into filings (accession_no, cik, form, filed_at) "
            "values ('000003408826000093', 34088, '10-Q', '2026-08-03')"
        )


async def test_tasks_get_the_normal_priority_by_default(
    conn: psycopg.AsyncConnection[Any],
) -> None:
    cur = await conn.execute("insert into tasks (kind, key) values ('k', '1') returning priority")

    assert await cur.fetchone() == (0,)


async def test_a_company_is_not_its_own_predecessor(conn: psycopg.AsyncConnection[Any]) -> None:
    await conn.execute("insert into companies (cik) values (34088)")

    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute(
            "insert into company_predecessors (cik, predecessor_cik, source_url, note) "
            "values (34088, 34088, 'https://www.sec.gov/', 'no')"
        )


async def test_a_fact_that_failed_holds_no_value(conn: psycopg.AsyncConnection[Any]) -> None:
    await conn.execute("insert into companies (cik) values (320193)")
    await conn.execute(
        "insert into earnings_releases (accession_no, cik, status, extractor, extractor_version, "
        "check_report) values ('0000320193-26-000018', 320193, 'partial', 'rules', '1', '{}')"
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute(
            "insert into earnings_facts (accession_no, slot, value, unit, method, "
            "extractor_version, check_status) values ('0000320193-26-000018', 'revenue', 1, "
            "'USD', 'rule', '1', 'failed')"
        )
