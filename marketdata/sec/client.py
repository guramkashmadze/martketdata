"""SEC HTTP client: the one way the code calls the SEC (PLAN.md §8, guardrail 6).

- Every request sends SEC_USER_AGENT; `SecClient.from_settings` refuses to start without it.
- One token bucket per process spaces all SEC requests 1/SEC_MAX_RPS apart (D-044).
- 429, 5xx and network errors are retried with exponential backoff and jitter, at most
  MAX_ATTEMPTS attempts in total; then `SecUnavailable` is raised and the task fails.
- Anything else that isn't a 2xx raises a typed `SecError` at once.
- Only SEC hosts are called, so the User-Agent (it holds a contact email) goes nowhere else.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
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

from marketdata.config import Settings, check_sec_user_agent, require_sec_user_agent

SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
MAX_ATTEMPTS = 5
TIMEOUT = httpx2.Timeout(30.0, connect=10.0)
# Waits of 1, 2, 4, 8 seconds, each plus up to 1 second of jitter.
BACKOFF = wait_exponential(multiplier=1, min=1, max=30) + wait_random(0, 1)
# A Retry-After longer than the backoff is honoured, up to this many seconds.
MAX_RETRY_AFTER = 60.0

Sleep = Callable[[float], Awaitable[None]]

log = structlog.get_logger(__name__)


class SecError(Exception):
    """An SEC request failed. `status_code` is None when no response arrived."""

    def __init__(self, message: str, url: str, status_code: int | None = None) -> None:
        super().__init__(message, url, status_code)
        self.message = message
        self.url = url
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class SecHostNotAllowed(SecError):
    """The URL is not on an SEC host; the client never sends the User-Agent elsewhere."""


class SecNotFound(SecError):
    """404: the file or company doesn't exist (yet)."""


class SecForbidden(SecError):
    """403: the SEC refused the request. It does this for undeclared tools and rate breaches."""


class SecRequestError(SecError):
    """Any other 4xx: the request itself is wrong; retrying won't help."""


class SecUnavailable(SecError):
    """429, 5xx or network errors on every attempt. The queue retries the task later."""


@dataclass(frozen=True)
class SecResponse:
    url: str
    status_code: int
    content: bytes
    content_type: str | None


class TokenBucket:
    """An async token bucket with a burst of one: requests start at least 1/rate apart.

    A burst equal to the rate would let 2 × rate requests into one second (16 at 8/s, over
    the SEC's 10/s); with a burst of one, no one-second window holds more than `rate`.
    Callers reserve their slot before awaiting, so concurrent tasks queue in order. There is
    no lock, so the bucket works across event loops; the worker runs one loop (D-007).
    """

    def __init__(
        self,
        rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next_slot = float("-inf")

    async def acquire(self) -> None:
        now = self._clock()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self._interval
        if slot > now:
            await self._sleep(slot - now)


_process_bucket: TokenBucket | None = None


def process_bucket(rate: float) -> TokenBucket:
    """The bucket every SecClient in this process shares."""
    global _process_bucket
    if _process_bucket is None:
        _process_bucket = TokenBucket(rate)
    elif _process_bucket.rate != rate:
        raise ValueError(
            f"this process already limits SEC requests to {_process_bucket.rate}/s; "
            f"one process has one SEC rate (asked for {rate}/s)"
        )
    return _process_bucket


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
    value = response.headers.get("retry-after", "")
    # The SEC is not known to send the HTTP-date form; it falls back to the backoff.
    return float(value) if value.strip().isdigit() else None


def _check_host(url: httpx2.URL) -> None:
    if url.scheme != "https" or url.host not in SEC_HOSTS:
        raise SecHostNotAllowed(
            f"{url} is not an SEC URL; the SEC client only calls https://"
            + " and https://".join(sorted(SEC_HOSTS)),
            str(url),
        )


async def _check_request_host(request: httpx2.Request) -> None:  # noqa: ASYNC124
    # Runs for every request, including redirects. AsyncClient hooks must be async.
    _check_host(request.url)


class SecClient:
    """Use as `async with SecClient.from_settings(settings) as sec: await sec.get(url)`."""

    def __init__(
        self,
        user_agent: str,
        *,
        bucket: TokenBucket,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._bucket = bucket
        self._sleep = sleep
        self._http = httpx2.AsyncClient(
            headers={
                "User-Agent": check_sec_user_agent(user_agent),
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=TIMEOUT,
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [_check_request_host]},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Fails with SettingsError when SEC_USER_AGENT is not set."""
        return cls(require_sec_user_agent(settings), bucket=process_bucket(settings.sec_max_rps))

    @property
    def timeout(self) -> httpx2.Timeout:
        return self._http.timeout

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, url: str) -> SecResponse:
        _check_host(httpx2.URL(url))
        retrying = AsyncRetrying(
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=_wait,
            retry=retry_if_exception_type(_Retryable),
            sleep=self._sleep,
            before_sleep=_log_retry(url),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    return await self._attempt(url)
        except _Retryable as exc:
            raise SecUnavailable(
                f"SEC request failed {MAX_ATTEMPTS} times, last with {exc}: {url}",
                url,
                exc.status_code,
            ) from exc
        raise AssertionError("unreachable: tenacity either returns or raises")

    async def _attempt(self, url: str) -> SecResponse:
        await self._bucket.acquire()
        try:
            response = await self._http.get(url)
        except httpx2.TransportError as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}", None, None) from exc

        status = response.status_code
        log.debug("sec_response", url=url, status=status, size=len(response.content))
        if response.is_success:
            return SecResponse(
                url=url,
                status_code=status,
                content=response.content,
                content_type=response.headers.get("content-type"),
            )
        if status == 429 or status >= 500:
            raise _Retryable(f"HTTP {status}", status, _retry_after(response))
        if status == 404:
            raise SecNotFound(f"SEC returned 404 Not Found: {url}", url, status)
        if status == 403:
            raise SecForbidden(
                f"SEC returned 403 Forbidden: {url}. The SEC answers 403 when the User-Agent "
                "is not declared or the request rate was exceeded (it then blocks for a few "
                "minutes). Check SEC_USER_AGENT and SEC_MAX_RPS before retrying.",
                url,
                status,
            )
        raise SecRequestError(f"SEC returned HTTP {status}: {url}", url, status)


def _log_retry(url: str) -> Callable[[RetryCallState], None]:
    def before_sleep(state: RetryCallState) -> None:
        error = state.outcome.exception() if state.outcome else None
        log.warning(
            "sec_retry",
            url=url,
            attempt=state.attempt_number,
            error=str(error),
            wait_seconds=round(state.upcoming_sleep, 2),
        )

    return before_sleep
