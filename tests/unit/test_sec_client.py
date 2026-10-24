"""E1-S1: the SEC client. All requests go to httpx2.MockTransport; nothing touches the network."""

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from marketdata.config import Settings, SettingsError, load_settings
from marketdata.sec import client as sec_client
from marketdata.sec.client import (
    MAX_ATTEMPTS,
    SecClient,
    SecError,
    SecForbidden,
    SecHostNotAllowed,
    SecNotFound,
    SecRequestError,
    SecUnavailable,
    TokenBucket,
    process_bucket,
)

UA = "marketdata-test ops@example.com"
URL = "https://data.sec.gov/submissions/CIK0000320193.json"
DB = "postgresql://marketdata:marketdata@localhost:5432/marketdata"

Handler = Callable[[httpx2.Request], httpx2.Response]


def make_settings(**values: Any) -> Settings:
    """Settings from these values and the environment, never from the developer's .env."""
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def fresh_process_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sec_client, "_process_bucket", None)


class Recorder:
    """A transport that answers from a list of handlers and records every request."""

    def __init__(self, *handlers: Handler) -> None:
        self.handlers = list(handlers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self.handlers.pop(0) if len(self.handlers) > 1 else self.handlers[0]
        return handler(request)


def status(code: int, headers: dict[str, str] | None = None) -> Handler:
    return lambda request: httpx2.Response(code, headers=headers, content=b"{}")


def make_client(
    transport: Recorder, sleeps: list[float] | None = None, rate: float = 1000.0
) -> SecClient:
    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        await asyncio.sleep(0)

    return SecClient(
        UA,
        bucket=TokenBucket(rate),
        transport=httpx2.MockTransport(transport),
        sleep=fake_sleep,
    )


# --- User-Agent and startup ---


async def test_every_request_sends_the_user_agent() -> None:
    transport = Recorder(status(200))
    async with make_client(transport) as sec:
        response = await sec.get(URL)
        await sec.get(URL)

    assert response.status_code == 200
    assert response.content == b"{}"
    assert [r.headers["User-Agent"] for r in transport.requests] == [UA, UA]
    assert transport.requests[0].headers["Accept-Encoding"] == "gzip, deflate"


def test_startup_fails_without_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    settings = make_settings(database_url=DB)

    with pytest.raises(SettingsError, match="SEC_USER_AGENT is missing"):
        SecClient.from_settings(settings)


def test_startup_fails_without_user_agent_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SEC_USER_AGENT", "")  # an empty value counts as not set

    with pytest.raises(SettingsError, match="SEC_USER_AGENT is missing"):
        SecClient.from_settings(load_settings(env_file=None))


@pytest.mark.parametrize("bad", ["marketdata", "ops@example.com", "marketdata ops@example"])
def test_user_agent_must_name_app_and_email(bad: str) -> None:
    with pytest.raises(ValueError, match="app name"):
        SecClient(bad, bucket=TokenBucket(1.0))


async def test_from_settings_uses_the_process_bucket() -> None:
    settings = make_settings(database_url=DB, sec_user_agent=UA)

    async with SecClient.from_settings(settings) as one, SecClient.from_settings(settings) as two:
        assert one._bucket is two._bucket is process_bucket(8.0)


def test_one_rate_per_process() -> None:
    process_bucket(8.0)

    with pytest.raises(ValueError, match="one process has one SEC rate"):
        process_bucket(4.0)


def test_timeouts_are_set() -> None:
    sec = make_client(Recorder(status(200)))

    assert sec.timeout.connect == 10.0
    assert sec.timeout.read == 30.0


# --- Rate limit ---


async def test_40_requests_take_at_least_40_over_rate_minus_one_seconds() -> None:
    """AC: 40 requests at SEC_MAX_RPS = 8 take at least 40 / 8 - 1 = 4 seconds (real time)."""
    rate = make_settings(database_url=DB).sec_max_rps
    transport = Recorder(status(200))
    sec = SecClient(UA, bucket=TokenBucket(rate), transport=httpx2.MockTransport(transport))

    started = time.monotonic()
    async with sec:
        await asyncio.gather(*(sec.get(URL) for _ in range(40)))
    elapsed = time.monotonic() - started

    assert rate == 8.0
    assert len(transport.requests) == 40
    assert elapsed >= 40 / rate - 1
    assert elapsed < 40 / rate + 1  # burst of one: about 39 / 8 = 4.9 seconds


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_no_one_second_window_holds_more_than_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(8.0, clock=clock, sleep=clock.sleep)
    starts: list[float] = []

    async def request() -> None:
        await bucket.acquire()
        starts.append(clock.now)

    for _ in range(40):
        await request()

    assert starts[0] == 100.0
    for i, start in enumerate(starts):
        in_window = [s for s in starts[i:] if s < start + 1.0]
        assert len(in_window) <= 8
    assert starts[-1] - starts[0] == pytest.approx(39 / 8)


async def test_idle_bucket_does_not_save_up_a_burst() -> None:
    clock = FakeClock()
    bucket = TokenBucket(8.0, clock=clock, sleep=clock.sleep)
    await bucket.acquire()
    clock.now += 60.0  # a minute of no requests

    await bucket.acquire()
    second = clock.now
    await bucket.acquire()

    assert clock.now - second == pytest.approx(1 / 8)


# --- Retries ---


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
async def test_retries_429_and_5xx_then_succeeds(code: int) -> None:
    transport = Recorder(status(code), status(code), status(200))
    sleeps: list[float] = []
    async with make_client(transport, sleeps) as sec:
        response = await sec.get(URL)

    assert response.status_code == 200
    assert len(transport.requests) == 3
    # Exponential backoff with up to one second of jitter: 1-2 s, then 2-3 s.
    assert 1 <= sleeps[0] <= 2
    assert 2 <= sleeps[1] <= 3


async def test_gives_up_after_five_attempts() -> None:
    transport = Recorder(status(503))
    sleeps: list[float] = []
    async with make_client(transport, sleeps) as sec:
        with pytest.raises(SecUnavailable) as excinfo:
            await sec.get(URL)

    assert MAX_ATTEMPTS == 5
    assert len(transport.requests) == 5
    assert len(sleeps) == 4
    for wait, base in zip(sleeps, [1, 2, 4, 8], strict=True):
        assert base <= wait <= base + 1
    assert excinfo.value.status_code == 503
    assert excinfo.value.url == URL


async def test_retry_after_is_honoured() -> None:
    transport = Recorder(status(429, {"Retry-After": "7"}), status(200))
    sleeps: list[float] = []
    async with make_client(transport, sleeps) as sec:
        await sec.get(URL)

    assert sleeps == [7.0]


async def test_network_errors_are_retried() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("read timed out", request=request)

    transport = Recorder(refuse, slow, status(200))
    async with make_client(transport, []) as sec:
        response = await sec.get(URL)

    assert response.status_code == 200
    assert len(transport.requests) == 3


async def test_network_errors_give_up_after_five_attempts() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    transport = Recorder(refuse)
    async with make_client(transport, []) as sec:
        with pytest.raises(SecUnavailable, match="ConnectError") as excinfo:
            await sec.get(URL)

    assert len(transport.requests) == 5
    assert excinfo.value.status_code is None


# --- Non-retryable errors ---


@pytest.mark.parametrize(
    ("code", "error"),
    [(404, SecNotFound), (403, SecForbidden), (400, SecRequestError), (410, SecRequestError)],
)
async def test_other_errors_raise_typed_exceptions_at_once(
    code: int, error: type[SecError]
) -> None:
    transport = Recorder(status(code))
    sleeps: list[float] = []
    async with make_client(transport, sleeps) as sec:
        with pytest.raises(error) as excinfo:
            await sec.get(URL)

    assert len(transport.requests) == 1
    assert sleeps == []
    assert excinfo.value.status_code == code


async def test_forbidden_says_what_to_check() -> None:
    async with make_client(Recorder(status(403))) as sec:
        with pytest.raises(SecForbidden, match="SEC_USER_AGENT and SEC_MAX_RPS"):
            await sec.get(URL)


# --- Hosts ---


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openfigi.com/v3/mapping",
        "http://www.sec.gov/files/company_tickers_exchange.json",
        "https://sec.gov.example.com/x.json",
    ],
)
async def test_only_sec_hosts_are_called(url: str) -> None:
    transport = Recorder(status(200))
    async with make_client(transport) as sec:
        with pytest.raises(SecHostNotAllowed):
            await sec.get(url)

    assert transport.requests == []


async def test_redirects_off_the_sec_are_refused() -> None:
    def redirect(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "https://example.com/elsewhere"})

    transport = Recorder(redirect, status(200))
    async with make_client(transport) as sec:
        with pytest.raises(SecHostNotAllowed):
            await sec.get(URL)

    assert [str(r.url) for r in transport.requests] == [URL]


async def test_redirects_within_the_sec_are_followed() -> None:
    target = "https://www.sec.gov/files/company_tickers.json"

    def redirect(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(301, headers={"Location": target})

    transport = Recorder(redirect, status(200))
    async with make_client(transport) as sec:
        response = await sec.get(URL)

    assert response.status_code == 200
    assert [str(r.url) for r in transport.requests] == [URL, target]
