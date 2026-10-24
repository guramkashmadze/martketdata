from typing import Any

import psycopg

from marketdata.db.repositories import tasks


async def test_insert_and_read_task(conn: psycopg.AsyncConnection[Any]) -> None:
    created = await tasks.insert(conn, "earnings_extract", "0000320193-24-000123", {"cik": 320193})

    assert created.status == "queued"
    assert created.attempts == 0
    assert created.payload == {"cik": 320193}
    assert created.run_after.tzinfo is not None
    assert await tasks.get(conn, created.task_id) == created


async def test_insert_is_idempotent_on_kind_and_key(conn: psycopg.AsyncConnection[Any]) -> None:
    first = await tasks.insert(conn, "facts_refresh", "320193", {"attempt": 1})
    again = await tasks.insert(conn, "facts_refresh", "320193", {"attempt": 2})
    other_kind = await tasks.insert(conn, "earnings_extract", "320193")

    assert again == first
    assert other_kind.task_id != first.task_id
    assert other_kind.payload == {}


async def test_get_missing_task(conn: psycopg.AsyncConnection[Any]) -> None:
    assert await tasks.get(conn, -1) is None
