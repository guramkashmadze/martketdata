"""Fetch from the SEC and keep the original, in one step (E1-S3).

Every ingest job downloads through `RawFetcher.fetch_and_store`, so no job can parse a file
it hasn't stored. The order is fixed: the SEC client fetches, the raw store saves the bytes,
then `raw_objects` records them. If the process dies between the last two steps, the file is
kept without a row; fetching the same filing document again adds the row (same key, same
bytes). An API response is stored under a new key each time, so it can be left without a row.

`fetch_if_changed` is for polling (E3, D-059): a response with the same bytes as the version
last processed is not stored again, and the caller doesn't parse it either.

`fetch_document` is for filing documents, which never change once filed: one stored before is
read from the raw store, so re-running an earnings extraction makes no SEC request (D-070).
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import raw_objects
from marketdata.db.repositories.raw_objects import RawObject
from marketdata.sec import urls
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
        await self._keep(url, key, response.content, response.content_type, fetched_at)
        return response.content

    async def fetch_url(self, url: str) -> tuple[str, bytes]:
        """Fetch and store a URL built by sec/urls.py under its standard key."""
        fetched_at = datetime.now(UTC)
        key = key_for_url(url, fetched_at)
        return key, await self.fetch_and_store(url, key, fetched_at=fetched_at)

    async def fetch_if_changed(self, url: str, previous_sha256: str | None) -> "Fetched":
        """Fetch a URL built by sec/urls.py. If its bytes have `previous_sha256` (the version
        last processed) nothing is stored; otherwise they are stored under the standard key
        before they are returned."""
        response = await self._sec.get(url)
        fetched_at = datetime.now(UTC)
        digest = sha256(response.content)
        if digest == previous_sha256:
            log.debug("raw_unchanged", url=url, sha256=digest)
            return Fetched(url, digest, fetched_at, key=None, data=None)
        key = key_for_url(url, fetched_at)
        await self._keep(url, key, response.content, response.content_type, fetched_at)
        return Fetched(url, digest, fetched_at, key=key, data=response.content)

    async def fetch_document(self, url: str) -> tuple[str, bytes, bool]:
        """A filing document (a URL built by urls.filing_document or urls.filing_index_page):
        its raw key, its bytes, and whether it was fetched now (False: read from the raw
        store, where an earlier fetch keeps it)."""
        parsed = urls.parse(url)
        if parsed is None or parsed.endpoint != "filing":
            raise ValueError(f"{url} is not a filing document")
        key = key_for_url(url, datetime.now(UTC))  # a filing key doesn't depend on the time
        async with self._connect() as conn:
            stored = await raw_objects.get(conn, key)
        if stored is not None:
            return key, await self._store.get(key), False
        return key, await self.fetch_and_store(url, key), True

    async def read(self, key: str) -> bytes:
        """A file stored earlier, from the raw store; no SEC request."""
        return await self._store.get(key)

    async def _keep(
        self, url: str, key: str, data: bytes, content_type: str | None, fetched_at: datetime
    ) -> None:
        """Save to the raw store, then record the raw_objects row."""
        digest = sha256(data)
        created = await self._store.put(key, data, content_type)
        row = RawObject(
            key=key,
            url=url,
            sha256=digest,
            size_bytes=len(data),
            content_type=content_type,
            fetched_at=fetched_at,
        )
        async with self._connect() as conn:
            stored = await raw_objects.upsert(conn, row)
        if stored.sha256 != digest:
            raise RawStoreError(
                f"raw_objects says {key} has sha256 {stored.sha256}, the store holds {digest}"
            )
        log.info("raw_stored", key=key, url=url, size_bytes=len(data), new=created)


@dataclass(frozen=True)
class Fetched:
    """One fetch by `fetch_if_changed`. `key` and `data` are None when the bytes were those of
    the version last processed, so nothing was stored."""

    url: str
    sha256: str
    fetched_at: datetime
    key: str | None
    data: bytes | None
