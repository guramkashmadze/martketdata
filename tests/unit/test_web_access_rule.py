"""E15-S1: the public UI's limit per address (PLAN.md §9.14) and which paths are pages."""

import pytest

from marketdata.service.errors import RateLimited
from marketdata.web.access import IpLimiter, is_logged_page, is_page


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_an_address_gets_its_requests_a_minute_and_then_waits() -> None:
    clock = Clock()
    limiter = IpLimiter(2, clock=clock)

    limiter.check("203.0.113.7")
    clock.now += 10
    limiter.check("203.0.113.7")
    limiter.check("198.51.100.4")  # another address has its own count
    with pytest.raises(RateLimited) as refused:
        limiter.check("203.0.113.7")

    assert refused.value.retry_after == 50  # until its first request is a minute old
    clock.now += 50
    limiter.check("203.0.113.7")


def test_a_refused_request_is_not_counted() -> None:
    clock = Clock()
    limiter = IpLimiter(1, clock=clock)
    limiter.check("203.0.113.7")
    for _ in range(5):
        clock.now += 10
        with pytest.raises(RateLimited):
            limiter.check("203.0.113.7")

    clock.now += 10  # a minute after the one counted request

    limiter.check("203.0.113.7")


def test_idle_addresses_are_forgotten() -> None:
    clock = Clock()
    limiter = IpLimiter(5, clock=clock)
    for number in range(100):
        limiter.check(f"203.0.113.{number}")
    assert limiter.addresses() == 100

    clock.now += 61
    limiter.check("198.51.100.4")

    assert limiter.addresses() == 1


@pytest.mark.parametrize(
    ("path", "page", "logged"),
    [
        ("/", True, True),
        ("/companies/AAPL", True, True),
        ("/proof/earnings/0000320193-26-000018", True, True),
        ("/static/app.css", True, False),
        ("/favicon.ico", True, False),
        ("/v1/companies/AAPL", False, False),
        ("/v1", False, False),
        ("/mcp", False, False),
        ("/health", False, False),
        ("/docs", False, False),
        ("/openapi.json", False, False),
        ("/v1x", True, True),
    ],
)
def test_pages_and_api_paths(path: str, page: bool, logged: bool) -> None:
    assert is_page(path) is page
    assert is_logged_page(path) is logged
