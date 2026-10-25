"""E3-S1: `marketdata tasks list|retry|purge-done`.

The CLI commits to the test database, so the tasks use a kind of their own and are deleted
afterwards.
"""

import asyncio
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli
from marketdata.db.repositories.tasks import Task
from marketdata.tasks import queue

LOCK = timedelta(minutes=5)


@pytest.fixture
def kind(migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    name = f"test-cli-{uuid4().hex[:8]}"
    yield name
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from tasks where kind = %s", [name])


def _seed(url: str, kind: str) -> dict[str, Task]:
    return asyncio.run(_seed_tasks(url, kind))


async def _seed_tasks(url: str, kind: str) -> dict[str, Task]:
    """Tasks of `kind` keyed done, dead, failed and queued, in those states."""
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        for key in ("done", "dead", "failed", "queued"):
            await queue.enqueue(conn, kind, key, {"n": key})
        claimed: dict[str, Task] = {}
        for _ in range(3):
            task = await queue.claim(conn, [kind], LOCK)
            assert task is not None
            claimed[task.key] = task
        await queue.complete(conn, claimed["done"])
        await queue.fail(conn, claimed["dead"], "SnapshotRefused: lists cut short", permanent=True)
        await queue.fail(conn, claimed["failed"], "SecUnavailable: HTTP 503")
        rows = await queue.list_tasks(conn, kind=kind)
    return {task.key: task for task in rows}


def _strip(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_list_counts_and_shows_errors(migrated_database: str, kind: str) -> None:
    seeded = _seed(migrated_database, kind)

    result = CliRunner().invoke(cli.app, ["tasks", "list", "--kind", kind])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert f"  {kind:<20} queued 1, done 1, failed 1, dead 1" in out
    assert "Most recently changed (4):" in out
    dead = seeded["dead"]
    assert re.search(rf"\b{dead.task_id}\s+{kind}\s+dead\s+dead\s+1\s", out)
    assert "SnapshotRefused: lists cut short" in out
    assert "SecUnavailable: HTTP 503" in out

    only_dead = CliRunner().invoke(cli.app, ["tasks", "list", "--kind", kind, "--status", "dead"])
    assert "Most recently changed (1):" in only_dead.stdout


def test_retry_by_id_and_all_dead(migrated_database: str, kind: str) -> None:
    seeded = _seed(migrated_database, kind)
    runner = CliRunner()

    by_id = runner.invoke(
        cli.app, ["tasks", "retry", str(seeded["failed"].task_id), str(seeded["done"].task_id)]
    )

    assert by_id.exit_code == 1
    assert f"  {seeded['failed'].task_id} {kind} failed" in by_id.stdout
    assert (
        f"Not retried: {seeded['done'].task_id} is done; only dead or failed tasks are."
        in by_id.stdout
    )

    all_dead = runner.invoke(cli.app, ["tasks", "retry", "--dead", "--kind", kind])
    assert all_dead.exit_code == 0, all_dead.output
    assert f"Queued again (1):\n  {seeded['dead'].task_id} {kind} dead" in all_dead.stdout

    nothing = runner.invoke(cli.app, ["tasks", "retry", "--dead", "--kind", kind])
    assert "Nothing to retry." in nothing.stdout


def test_retry_needs_ids_or_dead(kind: str) -> None:
    for args in ([], ["1", "--dead"], ["1", "--kind", "x"]):
        result = CliRunner().invoke(cli.app, ["tasks", "retry", *args])
        assert result.exit_code == 2
        assert "Give task IDs, or --dead (optionally with --kind)." in _strip(result.output)


def test_purge_done_keeps_recent_and_unfinished_tasks(migrated_database: str, kind: str) -> None:
    _seed(migrated_database, kind)
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute(
            "update tasks set updated_at = now() - interval '8 days' where kind = %s", [kind]
        )

    kept = CliRunner().invoke(cli.app, ["tasks", "purge-done", "--older-than-days", "9"])
    purged = CliRunner().invoke(cli.app, ["tasks", "purge-done"])

    assert kept.exit_code == 0 and "Deleted 0 done tasks unchanged for 9 days." in kept.stdout
    assert purged.exit_code == 0 and "Deleted 1 done tasks unchanged for 7 days." in purged.stdout
    with psycopg.connect(migrated_database) as conn:
        left = conn.execute("select key from tasks where kind = %s order by key", [kind])
        assert [row[0] for row in left] == ["dead", "failed", "queued"]
