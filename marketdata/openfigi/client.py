"""OpenFIGI client: the one way the code calls OpenFIGI (E9-S3, PLAN.md §9.8, D-121).

- One endpoint, POST https://api.openfigi.com/v3/mapping, a list of jobs in, one result per job
  out, in the same order. Without an API key a request holds at most 10 jobs; with one
  (OPENFIGI_API_KEY, sent as X-OPENFIGI-APIKEY), 100.
- Its own token bucket, not the SEC's: 25 requests a minute without a key, 250 with one.
- 429, 5xx and network errors are retried with exponential backoff, honouring Retry-After;
  then `OpenFigiUnavailable` is raised and the task fails. Any other error raises at once.
- Only api.openfigi.com is called, with the CUSIPs of 13F filings and nothing else: no
  User-Agent contact, nothing about a client or a user.
"""

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx2
import structlog
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from marketdata.config import Settings
from marketdata.sec.client import Sleep, TokenBucket

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
HOST = "api.openfigi.com"
MAX_ATTEMPTS = 5
TIMEOUT = httpx2.Timeout(30.0, connect=10.0)
BACKOFF = wait_exponential(multiplier=2, min=2, max=60) + wait_random(0, 1)
MAX_RETRY_AFTER = 120.0

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Limits:
    requests_per_minute: int
    jobs_per_request: int


WITHOUT_KEY = Limits(requests_per_minute=25, jobs_per_request=10)
WITH_KEY = Limits(requests_per_minute=250, jobs_per_request=100)


class OpenFigiError(Exception):
    """An OpenFIGI request failed. `status_code` is None when no response arrived."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, status_code)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class OpenFigiUnavailable(OpenFigiError):
    """429, 5xx or network errors on every attempt. The queue retries the task later."""


@dataclass(frozen=True)
class MappingResponse:
    """A mapping request as sent and its response as received, for the raw store."""

    request: bytes
    content: bytes
    content_type: str | None


class _Retryable(Exception):
    def __init__(self, reason: str, status_code: int | None, retry_after: float | None) -> None:
        super().__init__(reason, status_code, retry_after)
        self.reason = reason
        self.status_code = status_code
        self.retry_after = retry_after

    def __str__(self) -> str:
        return self.reason


def _wait(state: RetryCallState) -> float:
    backoff = BACKOFF(state)
    error = state.outcome.exception() if state.outcome else None
    if isinstance(error, _Retryable) and error.retry_after is not None:
        return max(backoff, min(error.retry_after, MAX_RETRY_AFTER))
    return backoff


def _retry_after(response: httpx2.Response) -> float | None:
    for name in ("retry-after", "ratelimit-reset"):
        value = response.headers.get(name, "").strip()
        if value.isdigit():
            return float(value)
    return None


async def _check_request_host(request: httpx2.Request) -> None:  # noqa: ASYNC124
    if request.url.scheme != "https" or request.url.host != HOST:
        raise OpenFigiError(f"{request.url} is not OpenFIGI's API; only https://{HOST} is called")


_process_buckets: dict[int, TokenBucket] = {}


def process_bucket(limits: Limits) -> TokenBucket:
    """The bucket every OpenFIGI client of this process with these limits shares."""
    rate = limits.requests_per_minute / 60
    if limits.requests_per_minute not in _process_buckets:
        _process_buckets[limits.requests_per_minute] = TokenBucket(rate)
    return _process_buckets[limits.requests_per_minute]


class OpenFigiClient:
    """Use as `async with OpenFigiClient.from_settings(settings) as figi: await figi.map(jobs)`."""

    def __init__(
        self,
        api_key: str | None,
        *,
        bucket: TokenBucket | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.limits = WITH_KEY if api_key else WITHOUT_KEY
        self._bucket = bucket or process_bucket(self.limits)
        self._sleep = sleep
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["X-OPENFIGI-APIKEY"] = api_key
        self._http = httpx2.AsyncClient(
            headers=headers,
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=transport,
            event_hooks={"request": [_check_request_host]},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        key = settings.openfigi_api_key
        return cls(key.get_secret_value() if key else None)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def map(self, jobs: Sequence[Mapping[str, str]]) -> MappingResponse:
        """One mapping request; at most `limits.jobs_per_request` jobs."""
        if not 0 < len(jobs) <= self.limits.jobs_per_request:
            raise ValueError(
                f"a mapping request holds 1 to {self.limits.jobs_per_request} jobs, "
                f"got {len(jobs)}"
            )
        body = json.dumps([dict(job) for job in jobs], separators=(",", ":")).encode()
        retrying = AsyncRetrying(
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=_wait,
            retry=retry_if_exception_type(_Retryable),
            sleep=self._sleep,
            before_sleep=_log_retry,
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    return await self._attempt(body)
        except _Retryable as exc:
            raise OpenFigiUnavailable(
                f"OpenFIGI request failed {MAX_ATTEMPTS} times, last with {exc}", exc.status_code
            ) from exc
        raise AssertionError("unreachable: tenacity either returns or raises")

    async def _attempt(self, body: bytes) -> MappingResponse:
        await self._bucket.acquire()
        try:
            response = await self._http.post(MAPPING_URL, content=body)
        except httpx2.TransportError as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}", None, None) from exc
        status = response.status_code
        log.debug("openfigi_response", status=status, size=len(response.content))
        if response.is_success:
            return MappingResponse(body, response.content, response.headers.get("content-type"))
        if status == 429 or status >= 500:
            raise _Retryable(f"HTTP {status}", status, _retry_after(response))
        raise OpenFigiError(f"OpenFIGI returned HTTP {status}: {response.text[:200]}", status)


def _log_retry(state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome else None
    log.warning(
        "openfigi_retry",
        attempt=state.attempt_number,
        error=str(error),
        wait_seconds=round(state.upcoming_sleep, 2),
    )
