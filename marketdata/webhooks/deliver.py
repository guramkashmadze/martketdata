"""Delivering webhook events (E5b-S1, PLAN.md §9.11): the `webhook_deliver` task.

One attempt POSTs the event's stored body to the destination, signed with its secret
(webhooks/signing.py). Success is any 2xx within 10 seconds. Anything else fails the attempt
and the queue tries again about 1 and 10 minutes later, three attempts in all; then the
delivery is dead. A delivery whose every attempt failed counts against its destination: 50 in a
row switch it off and log an error for the operator. Each attempt is logged in
webhook_deliveries.

The host is resolved at every attempt and the connection goes to an address checked there
(public addresses only; in dev, this machine too), with the URL's name kept for the Host header
and TLS. A name that changes its address between the check and the connection can't move the
request elsewhere. Redirects aren't followed: a 3xx is a failed attempt.
"""

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx2
import structlog
from psycopg import AsyncConnection

from marketdata.config import Settings, SettingsError, require_webhook_key
from marketdata.normalize.webhooks import (
    check_url,
    is_ip_address,
    netloc_of,
    refused_address,
)
from marketdata.service import webhooks
from marketdata.webhooks.signing import (
    SIGNATURE_HEADER,
    SecretUnreadable,
    decrypt_secret,
    signature,
)

TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3
USER_AGENT = "marketdata-webhooks/1"
MAX_ERROR = 500  # characters of an error kept

# A pool's `connection` method: a connection whose transaction commits on exit.
type Connect = Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]
# Host and port to addresses; the tests replace the system resolver.
type Resolver = Callable[[str, int], Awaitable[list[str]]]

log = structlog.get_logger(__name__)


class DeliveryRefused(Exception):
    """Trying again can't help: the destination is switched off, its URL or address is
    refused, or the worker can't read its secret. The delivery is dead at once."""


class DeliveryFailed(Exception):
    """The receiver didn't answer 2xx within 10 seconds; the queue tries again later."""


class _Refused(Exception):
    """The destination's URL or address is refused (counted against it)."""


@dataclass(frozen=True)
class Attempt:
    status_code: int | None
    error: str | None  # None when delivered
    duration_ms: int


@dataclass(frozen=True)
class _Target:
    url: str  # with the host replaced by the checked address
    host: str  # the Host header: the URL's host and port
    server_name: str | None  # for TLS (SNI and the certificate check): the URL's host name


async def resolve(host: str, port: int) -> list[str]:
    """The host's addresses, as the system resolver gives them."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def deliver(
    connect: Connect,
    settings: Settings,
    event_id: UUID,
    dest_id: int,
    attempt: int,
    *,
    final: bool,
    transport: httpx2.AsyncBaseTransport | None = None,
    resolver: Resolver = resolve,
    now: float | None = None,
) -> Attempt:
    """One attempt to deliver an event to a destination. `final`: the queue won't try again.
    Raises DeliveryFailed when the attempt failed, DeliveryRefused when no attempt can work."""
    async with connect() as conn:
        target = await webhooks.delivery_target(conn, event_id, dest_id)
    if not target.active:
        raise DeliveryRefused(f"destination {dest_id} is switched off")
    started = time.perf_counter()
    try:
        secret = decrypt_secret(require_webhook_key(settings), target.secret_enc)
    except (SettingsError, SecretUnreadable) as exc:
        # Our configuration, not the destination's fault: nothing is counted against it.
        raise DeliveryRefused(str(exc)) from exc
    pinned: _Target | _Refused | OSError
    try:
        pinned = await _pin(target.url, settings.env == "dev", resolver)
    except (_Refused, OSError) as exc:  # OSError: the name doesn't resolve now
        pinned = exc
    if isinstance(pinned, _Refused):  # counted against the destination at once
        refused = Attempt(None, f"refused: {pinned}", 0)
        await _record(connect, event_id, dest_id, attempt, refused, True)
        raise DeliveryRefused(str(pinned)) from pinned
    if isinstance(pinned, OSError):
        unresolved = f"{target.url}: no address: {pinned}"
        await _record(connect, event_id, dest_id, attempt, Attempt(None, unresolved, 0), final)
        raise DeliveryFailed(unresolved) from pinned
    body = target.body.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Host": pinned.host,
        SIGNATURE_HEADER: signature(secret, body, int(now if now is not None else time.time())),
    }
    extensions = {"sni_hostname": pinned.server_name} if pinned.server_name else {}
    status: int | None = None
    error: str | None = None
    try:
        async with (
            asyncio.timeout(TIMEOUT_SECONDS),
            httpx2.AsyncClient(
                transport=transport,
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "POST", pinned.url, content=body, headers=headers, extensions=extensions
            ) as response,
        ):
            status = response.status_code  # the body isn't read
    except TimeoutError:
        error = f"no answer within {TIMEOUT_SECONDS:.0f} seconds"
    except httpx2.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
    if error is None and status is not None and not 200 <= status < 300:
        error = f"answered {status}"
    outcome = Attempt(status, _cut(error), round((time.perf_counter() - started) * 1000))
    await _record(connect, event_id, dest_id, attempt, outcome, final)
    if outcome.error is not None:
        raise DeliveryFailed(f"{target.url}: {outcome.error}")
    return outcome


async def _pin(url: str, dev: bool, resolver: Resolver) -> _Target:
    try:
        checked = check_url(url, allow_loopback=dev)
    except ValueError as exc:
        raise _Refused(str(exc)) from None
    parts = urlsplit(checked)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    literal = is_ip_address(host)
    addresses = [host] if literal else await resolver(host, port)
    reasons = {address: _refusal(address, dev) for address in addresses}
    allowed = [address for address, reason in reasons.items() if reason is None]
    if not allowed:
        found = ", ".join(f"{address} ({reason})" for address, reason in reasons.items())
        raise _Refused(f"{host} resolves to no public address: {found or 'none'}")
    return _Target(
        url=urlunsplit(
            (parts.scheme, netloc_of(allowed[0], parts.port), parts.path, parts.query, "")
        ),
        host=netloc_of(host, parts.port),
        server_name=host if parts.scheme == "https" and not literal else None,
    )


def _refusal(address: str, dev: bool) -> str | None:
    try:
        return refused_address(address, allow_loopback=dev)
    except ValueError:  # e.g. a link-local address with a zone, fe80::1%en0
        return "not a usable address"


async def _record(
    connect: Connect, event_id: UUID, dest_id: int, attempt: int, outcome: Attempt, final: bool
) -> None:
    async with connect() as conn, conn.transaction():
        count = await webhooks.record_attempt(
            conn,
            event_id,
            dest_id,
            attempt,
            status_code=outcome.status_code,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
            final=final,
        )
    if (
        count is not None
        and not count.active
        and count.consecutive_failures == webhooks.FAILURES_TO_DISABLE
    ):
        log.error(
            "webhook_destination_disabled",
            dest_id=dest_id,
            failures=count.consecutive_failures,
            reason=webhooks.AUTO_DISABLED,
        )


def _cut(error: str | None) -> str | None:
    if error is None or len(error) <= MAX_ERROR:
        return error
    return error[: MAX_ERROR - 1] + "…"
