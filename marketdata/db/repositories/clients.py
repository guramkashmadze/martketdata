"""API clients, their keys and the request log (PLAN.md §7: clients, api_keys, api_requests; E5a).

A key is stored only as the sha256 of the key (`key_hash`); the key itself is shown once.
"""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


class Client(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: int
    name: str
    created_at: datetime
    active_keys: int = 0


class ApiKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_id: int
    client_id: int
    client_name: str
    scope: str
    rate_per_min: int
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class KeyAccess(BaseModel):
    """A key looked up by its hash, with the requests it made in the rate-limit window."""

    model_config = ConfigDict(frozen=True)

    key_id: int
    client_id: int
    scope: str
    rate_per_min: int
    revoked_at: datetime | None
    recent: int  # requests since the window's start, those answered 429 left out
    oldest: datetime | None  # the first of them


_INSERT_CLIENT = """
    insert into clients (name) values (%(name)s)
    on conflict (name) do nothing
    returning client_id, name, created_at
"""
_CLIENTS = """
    select c.client_id, c.name, c.created_at,
           count(k.key_id) filter (where k.revoked_at is null) as active_keys
    from clients c left join api_keys k using (client_id)
    where %(name)s::text is null or c.name = %(name)s
    group by c.client_id
    order by c.client_id
"""
_INSERT_KEY = """
    insert into api_keys (client_id, scope, key_hash, rate_per_min)
    values (%(client_id)s, %(scope)s, %(key_hash)s, %(rate_per_min)s)
    returning key_id
"""
_KEYS = """
    select k.key_id, k.client_id, c.name as client_name, k.scope, k.rate_per_min, k.created_at,
           k.revoked_at,
           (select max(r.at) from api_requests r where r.key_id = k.key_id) as last_used_at
    from api_keys k join clients c using (client_id)
    where (%(client_id)s::bigint is null or k.client_id = %(client_id)s)
      and (%(key_id)s::bigint is null or k.key_id = %(key_id)s)
    order by k.key_id
"""
# A key revoked twice keeps the first time.
_REVOKE = """
    update api_keys set revoked_at = coalesce(revoked_at, now()) where key_id = %(key_id)s
"""
_ACCESS = """
    select k.key_id, k.client_id, k.scope, k.rate_per_min, k.revoked_at,
           count(r.id) as recent, min(r.at) as oldest
    from api_keys k
    left join api_requests r
      on r.key_id = k.key_id and r.at > %(since)s and r.status <> 429
    where k.key_hash = %(key_hash)s
    group by k.key_id
"""
_RECORD = """
    insert into api_requests (key_id, at, method, path, status, duration_ms)
    values (%(key_id)s, %(at)s, %(method)s, %(path)s, %(status)s, %(duration_ms)s)
"""


async def insert_client(conn: AsyncConnection[Any], name: str) -> Client | None:
    """The new client, or None when one with that name exists."""
    async with conn.cursor(row_factory=class_row(Client)) as cur:
        await cur.execute(_INSERT_CLIENT, {"name": name})
        return await cur.fetchone()


async def clients(conn: AsyncConnection[Any], name: str | None = None) -> list[Client]:
    """Every client (or the one with this name), with its number of keys not revoked."""
    async with conn.cursor(row_factory=class_row(Client)) as cur:
        await cur.execute(_CLIENTS, {"name": name})
        return await cur.fetchall()


async def insert_key(
    conn: AsyncConnection[Any], client_id: int, scope: str, key_hash: str, rate_per_min: int
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            _INSERT_KEY,
            {
                "client_id": client_id,
                "scope": scope,
                "key_hash": key_hash,
                "rate_per_min": rate_per_min,
            },
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"no key was inserted for client {client_id}")
    return int(row[0])


async def keys(
    conn: AsyncConnection[Any], *, client_id: int | None = None, key_id: int | None = None
) -> list[ApiKey]:
    """Keys by id, of one client or all, with the time each was last used."""
    async with conn.cursor(row_factory=class_row(ApiKey)) as cur:
        await cur.execute(_KEYS, {"client_id": client_id, "key_id": key_id})
        return await cur.fetchall()


async def revoke(conn: AsyncConnection[Any], key_id: int) -> bool:
    """Revoke a key; False when no key has this id."""
    async with conn.cursor() as cur:
        await cur.execute(_REVOKE, {"key_id": key_id})
        return cur.rowcount > 0


async def access(conn: AsyncConnection[Any], key_hash: str, since: datetime) -> KeyAccess | None:
    """The key with this hash and its requests after `since`; None for a key we never issued."""
    async with conn.cursor(row_factory=class_row(KeyAccess)) as cur:
        await cur.execute(_ACCESS, {"key_hash": key_hash, "since": since})
        return await cur.fetchone()


async def record_request(
    conn: AsyncConnection[Any],
    *,
    key_id: int | None,
    at: datetime,
    method: str,
    path: str,
    status: int,
    duration_ms: int,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            _RECORD,
            {
                "key_id": key_id,
                "at": at,
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
            },
        )
