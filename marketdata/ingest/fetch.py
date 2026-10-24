"""Fetch from the SEC and keep the original, in one step (E1-S3).

Every ingest job downloads through `RawFetcher.fetch_and_store`, so no job can parse a file
it hasn't stored. The order is fixed: the SEC client fetches, the raw store saves the bytes,
then `raw_objects` records them. If the process dies between the last two steps, the file is
kept without a row; fetching the same filing document again adds the row (same key, same
bytes). An API response is stored under a new key each time, so it can be left without a row.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import raw_objects
from marketdata.db.repositories.raw_objects import RawObject
from marketdata.sec.client import SecClient
from marketdata.sec.raw_store import RawStore, RawStoreError, key_for_url, sha256

# A pool's `connection` method: a connection whose transaction commits on exit.
Connect = Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]

log = structlog.get_logger(__name__)


class RawFetcher:
    def __init__(self, sec: SecClient, store: RawStore, connect: Connect) -> None:
        self._sec = sec
        self._store = store
        self._connect = connect

    async def fetch_and_store(
        self, url: str, key: str, *, fetched_at: datetime | None = None
    ) -> bytes:
        """Fetch `url`, save it at `key`, record it in raw_objects, then return the bytes.

        `fetched_at` defaults to when the response arrived; pass the time the key was named
        after, so the row and the key agree.
        """
        response = await self._sec.get(url)
        fetched_at = fetched_at or datetime.now(UTC)
        data = response.content
        digest = sha256(data)

        created = await self._store.put(key, data, response.content_type)
        row = RawObject(
            key=key,
            url=url,
            sha256=digest,
            size_bytes=len(data),
            content_type=response.content_type,
            fetched_at=fetched_at,
        )
        async with self._connect() as conn:
            stored = await raw_objects.upsert(conn, row)
        if stored.sha256 != digest:
            raise RawStoreError(
                f"raw_objects says {key} has sha256 {stored.sha256}, the store holds {digest}"
            )

        log.info("raw_stored", key=key, url=url, size_bytes=len(data), new=created)
        return data

    async def fetch_url(self, url: str) -> tuple[str, bytes]:
        """Fetch and store a URL built by sec/urls.py under its standard key."""
        fetched_at = datetime.now(UTC)
        key = key_for_url(url, fetched_at)
        return key, await self.fetch_and_store(url, key, fetched_at=fetched_at)
