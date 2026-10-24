"""E1-S3: fetch_and_store keeps the original in the raw store, then records it in raw_objects."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest

from marketdata.db.repositories import raw_objects
from marketdata.ingest import fetch as fetch_module
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.sec import urls
from marketdata.sec.client import SecClient, SecNotFound, TokenBucket
from marketdata.sec.raw_store import LocalRawStore, RawObjectConflict, fetched_at_name, sha256

UA = "marketdata-test ops@example.com"
AAPL = 320193
ACCESSION = "0000320193-24-000123"
INDEX_URL = urls.filing_index_json(AAPL, ACCESSION)
INDEX_KEY = "raw/sec/320193/0000320193-24-000123/index.json"
BODY = b'{"directory": {"item": []}}'


def connect_to(conn: psycopg.AsyncConnection[Any]) -> Connect:
    """Hand out the test's connection; the test rolls its transaction back afterwards."""

    @asynccontextmanager
    async def connect() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        yield conn

    return connect


def sec_returning(*bodies: bytes, status: int = 200) -> SecClient:
    responses = list(bodies)

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = responses.pop(0) if len(responses) > 1 else responses[0]
        return httpx2.Response(status, content=body, headers={"Content-Type": "application/json"})

    return SecClient(UA, bucket=TokenBucket(1000.0), transport=httpx2.MockTransport(handler))


@pytest.fixture
def store(tmp_path: Path) -> LocalRawStore:
    return LocalRawStore(tmp_path)


async def test_fetch_and_store_saves_the_file_and_records_it(
    conn: psycopg.AsyncConnection[Any], store: LocalRawStore
) -> None:
    fetcher = RawFetcher(sec_returning(BODY), store, connect_to(conn))
    before = datetime.now(UTC)

    data = await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY)

    assert data == BODY
    assert await store.get(INDEX_KEY) == BODY
    row = await raw_objects.get(conn, INDEX_KEY)
    assert row is not None
    assert row.url == INDEX_URL
    assert row.sha256 == sha256(BODY)
    assert row.size_bytes == len(BODY)
    assert row.content_type == "application/json"
    assert before <= row.fetched_at <= datetime.now(UTC)


async def test_the_raw_file_is_saved_before_the_database_row(
    conn: psycopg.AsyncConnection[Any], store: LocalRawStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def database_down(*args: object) -> None:  # noqa: ASYNC124 - replaces an async function
        raise psycopg.OperationalError("database is down")

    monkeypatch.setattr(fetch_module.raw_objects, "upsert", database_down)
    fetcher = RawFetcher(sec_returning(BODY), store, connect_to(conn))

    with pytest.raises(psycopg.OperationalError):
        await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY)

    assert await store.get(INDEX_KEY) == BODY


async def test_fetching_again_is_idempotent(
    conn: psycopg.AsyncConnection[Any], store: LocalRawStore
) -> None:
    fetcher = RawFetcher(sec_returning(BODY), store, connect_to(conn))
    await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY)
    first = await raw_objects.get(conn, INDEX_KEY)

    assert await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY) == BODY

    assert await raw_objects.get(conn, INDEX_KEY) == first  # fetched_at stays the first fetch
    cur = await conn.execute("select count(*) from raw_objects where key = %s", [INDEX_KEY])
    assert await cur.fetchone() == (1,)


async def test_changed_bytes_at_the_same_key_are_refused(
    conn: psycopg.AsyncConnection[Any], store: LocalRawStore
) -> None:
    fetcher = RawFetcher(sec_returning(BODY, b"changed"), store, connect_to(conn))
    await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY)

    with pytest.raises(RawObjectConflict):
        await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY)

    assert await store.get(INDEX_KEY) == BODY
    row = await raw_objects.get(conn, INDEX_KEY)
    assert row is not None and row.sha256 == sha256(BODY)


async def test_failed_requests_store_nothing(
    conn: psycopg.AsyncConnection[Any], store: LocalRawStore
) -> None:
    fetcher = RawFetcher(sec_returning(b"not found", status=404), store, connect_to(conn))

    with pytest.raises(SecNotFound):
        await fetcher.fetch_and_store(INDEX_URL, INDEX_KEY)

    assert not await store.exists(INDEX_KEY)
    assert await raw_objects.get(conn, INDEX_KEY) is None


async def test_api_responses_get_a_new_key_per_fetch(
    conn: psycopg.AsyncConnection[Any], store: LocalRawStore
) -> None:
    fetcher = RawFetcher(sec_returning(b'{"v": 1}', b'{"v": 2}'), store, connect_to(conn))

    key1, data1 = await fetcher.fetch_url(urls.submissions(AAPL))
    key2, data2 = await fetcher.fetch_url(urls.submissions(AAPL))

    assert key1 != key2
    assert (data1, data2) == (b'{"v": 1}', b'{"v": 2}')
    for key in (key1, key2):
        row = await raw_objects.get(conn, key)
        assert row is not None
        # The key is named after the row's fetch time.
        assert key == f"raw/sec/api/submissions/320193/{fetched_at_name(row.fetched_at)}.json"


async def test_raw_objects_checks_sha256_and_size(conn: psycopg.AsyncConnection[Any]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute(
            "insert into raw_objects (key, url, sha256, size_bytes) values ('k', 'u', 'XYZ', 1)"
        )
