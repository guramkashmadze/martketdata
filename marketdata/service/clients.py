"""API clients and keys (E5a-S1): the operator creates them with the CLI, and the API checks each
request's key, its scope and its rate (PLAN.md §10.1).

A key is `mdk_` and 32 random bytes in URL-safe base64. It is shown once and stored only as its
sha256, so the database holds no key. A request is allowed when the key exists, isn't revoked,
has the scope the request needs, and made fewer than `rate_per_min` requests in the minute
before it, those answered 429 left out (a sliding window over the request log, D-095).
"""

import hashlib
import math
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import clients as repository
from marketdata.db.repositories.clients import ApiKey, Client
from marketdata.service.errors import (
    Forbidden,
    InvalidParameter,
    NotFound,
    RateLimited,
    Unauthorized,
)

KEY_PREFIX = "mdk_"
KEY_BYTES = 32
DEFAULT_RATE_PER_MIN = 120  # the column's default (§7)
WINDOW = timedelta(minutes=1)

type Scope = Literal["api", "review"]  # review: E7b
SCOPES: tuple[Scope, ...] = ("api", "review")


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(KEY_BYTES)


def hash_key(key: str) -> str:
    """What `api_keys.key_hash` stores: the key's sha256 in lowercase hex."""
    return hashlib.sha256(key.encode()).hexdigest()


async def create_client(conn: AsyncConnection[Any], name: str) -> tuple[Client, bool]:
    """The client with this name and whether it was created now; creating it twice changes
    nothing."""
    name = name.strip()
    if not name:
        raise InvalidParameter("A client needs a name, e.g. the company or app the keys are for.")
    created = await repository.insert_client(conn, name)
    if created is not None:
        return created, True
    return await get_client(conn, name), False


async def get_client(conn: AsyncConnection[Any], name: str) -> Client:
    found = await repository.clients(conn, name)
    if not found:
        raise NotFound(f"No client is named {name!r}. `marketdata clients list` shows them.")
    return found[0]


async def list_clients(conn: AsyncConnection[Any]) -> list[Client]:
    return await repository.clients(conn)


class NewKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str  # shown once; only its hash is stored
    stored: ApiKey


async def create_key(
    conn: AsyncConnection[Any],
    client_name: str,
    *,
    scope: str = "api",
    rate_per_min: int = DEFAULT_RATE_PER_MIN,
) -> NewKey:
    if scope not in SCOPES:
        raise InvalidParameter(f"scope must be api or review, not {scope!r}")
    if rate_per_min < 1:
        raise InvalidParameter(f"rate_per_min must be at least 1, got {rate_per_min}")
    owner = await get_client(conn, client_name)
    key = new_key()
    key_id = await repository.insert_key(conn, owner.client_id, scope, hash_key(key), rate_per_min)
    (stored,) = await repository.keys(conn, key_id=key_id)
    return NewKey(key=key, stored=stored)


async def list_keys(conn: AsyncConnection[Any], client_name: str | None = None) -> list[ApiKey]:
    """Every key, or one client's, revoked ones included."""
    client_id = (await get_client(conn, client_name)).client_id if client_name else None
    return await repository.keys(conn, client_id=client_id)


async def revoke_key(conn: AsyncConnection[Any], key_id: int) -> ApiKey:
    """Revoke a key: its next request is refused (401). Revoking it again changes nothing."""
    if not await repository.revoke(conn, key_id):
        raise NotFound(f"No API key has id {key_id}. `marketdata keys list` shows them.")
    (stored,) = await repository.keys(conn, key_id=key_id)
    return stored


class Caller(BaseModel):
    """The key a request was made with."""

    model_config = ConfigDict(frozen=True)

    key_id: int
    client_id: int
    scope: str
    rate_per_min: int


async def authenticate(
    conn: AsyncConnection[Any],
    key: str | None,
    *,
    scope: Scope = "api",
    now: datetime | None = None,
) -> Caller:
    """The caller, when its key may make a request that needs `scope` now.

    Raises Unauthorized without a key, for a key we never issued and for a revoked key;
    Forbidden for a key of another scope; RateLimited when the key made `rate_per_min` requests
    in the minute before `now` (default: now, UTC), with the seconds until one leaves it."""
    key = (key or "").strip()
    if not key:
        raise Unauthorized("Send your API key in the X-API-Key header.")
    now = now or datetime.now(UTC)
    found = await repository.access(conn, hash_key(key), now - WINDOW)
    if found is None:
        raise Unauthorized("This API key is not valid. Check that it was copied in full.")
    if found.revoked_at is not None:
        raise Unauthorized("This API key was revoked. Ask for a new one.", found.key_id)
    if found.scope != scope:
        raise Forbidden(
            f"This key has the {found.scope!r} scope; this request needs a key with the "
            f"{scope!r} scope.",
            found.key_id,
        )
    if found.recent >= found.rate_per_min:
        oldest = found.oldest or now
        wait = max(1, math.ceil((oldest + WINDOW - now).total_seconds()))
        raise RateLimited(
            f"This key made {found.recent} requests in the last minute; its limit is "
            f"{found.rate_per_min}. Retry in {wait} seconds.",
            found.key_id,
            wait,
        )
    return Caller(
        key_id=found.key_id,
        client_id=found.client_id,
        scope=found.scope,
        rate_per_min=found.rate_per_min,
    )


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
    """One `api_requests` row: the key (None when no valid key was sent), when the request
    started, its method and path (without the query string), the status and how long it took."""
    await repository.record_request(
        conn,
        key_id=key_id,
        at=at,
        method=method,
        path=path,
        status=status,
        duration_ms=duration_ms,
    )
