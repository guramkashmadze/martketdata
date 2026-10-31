"""E9-S3: the OpenFIGI client posts at most a request's jobs, sends the key only when there is
one, keeps its own rate limit and retries 429 after the time OpenFIGI asks for (D-121)."""

import json

import httpx2
import pytest

from marketdata.openfigi.client import (
    MAPPING_URL,
    WITH_KEY,
    WITHOUT_KEY,
    OpenFigiClient,
    OpenFigiError,
    OpenFigiUnavailable,
    process_bucket,
)
from marketdata.sec.client import TokenBucket

JOB = {"idType": "ID_CUSIP", "idValue": "037833100", "exchCode": "US"}


class Recorder:
    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []
        self.waits: list[float] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def _client(recorder: Recorder, key: str | None = None) -> OpenFigiClient:
    return OpenFigiClient(
        key,
        bucket=TokenBucket(1e6),
        transport=httpx2.MockTransport(recorder.handle),
        sleep=recorder.sleep,
    )


async def test_a_request_posts_the_jobs_as_json_and_returns_both_bodies() -> None:
    recorder = Recorder(httpx2.Response(200, json=[{"warning": "No identifier found."}]))

    async with _client(recorder) as client:
        response = await client.map([JOB])

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("POST", MAPPING_URL)
    assert json.loads(request.content) == [JOB] == json.loads(response.request)
    assert "x-openfigi-apikey" not in request.headers
    assert "@" not in request.headers.get("user-agent", "")  # no SEC contact email
    assert json.loads(response.content) == [{"warning": "No identifier found."}]


async def test_a_key_is_sent_and_raises_the_limits() -> None:
    recorder = Recorder(httpx2.Response(200, json=[{}] * 100))

    async with _client(recorder, key="test-key") as client:
        assert client.limits == WITH_KEY
        await client.map([JOB] * 100)

    assert recorder.requests[0].headers["x-openfigi-apikey"] == "test-key"


async def test_more_jobs_than_a_request_holds_are_refused_before_sending() -> None:
    recorder = Recorder()

    async with _client(recorder) as client:
        assert client.limits == WITHOUT_KEY
        with pytest.raises(ValueError, match="1 to 10 jobs, got 11"):
            await client.map([JOB] * 11)

    assert recorder.requests == []


async def test_429_is_retried_after_the_wait_openfigi_asks_for() -> None:
    recorder = Recorder(
        httpx2.Response(429, headers={"ratelimit-reset": "40"}),
        httpx2.Response(200, json=[{}]),
    )

    async with _client(recorder) as client:
        await client.map([JOB])

    assert len(recorder.requests) == 2 and recorder.waits[0] >= 40


async def test_an_outage_raises_unavailable_and_a_bad_request_at_once() -> None:
    down = Recorder(*[httpx2.Response(503)] * 5)
    bad = Recorder(httpx2.Response(400, text="Invalid idType"))

    async with _client(down) as client:
        with pytest.raises(OpenFigiUnavailable, match="failed 5 times"):
            await client.map([JOB])
    async with _client(bad) as client:
        with pytest.raises(OpenFigiError, match="HTTP 400: Invalid idType"):
            await client.map([JOB])

    assert len(bad.requests) == 1


def test_one_rate_limit_per_process() -> None:
    assert process_bucket(WITHOUT_KEY) is process_bucket(WITHOUT_KEY)
    assert process_bucket(WITHOUT_KEY).rate == pytest.approx(25 / 60)
    assert process_bucket(WITH_KEY).rate == pytest.approx(250 / 60)
