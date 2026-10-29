"""Domain exceptions raised by service functions (PLAN.md §6). api/errors.py maps them to the
error envelope (§10.1): InvalidParameter to 400, Unauthorized to 401, Forbidden to 403,
NotFound to 404, NotApplicable to 422, RateLimited to 429. Messages say how to fix the request
where possible."""


class DomainError(Exception):
    def __init__(self, message: str, *details: object) -> None:
        super().__init__(message, *details)
        self.message = message

    def __str__(self) -> str:
        return self.message


class InvalidParameter(DomainError):
    """The request can't be right as written, e.g. a ticker with characters no ticker has."""


class NotFound(DomainError):
    """Nothing matches, e.g. a ticker no SEC list has."""


class NotApplicable(DomainError):
    """The data kind doesn't apply to the company, e.g. fundamentals of a fund (§9.1, D-018)."""


class AccessDenied(DomainError):
    """A request the key can't make. `key_id` names the key when it was found, so the request
    log can count it; `retry_after` is set on RateLimited."""

    def __init__(
        self, message: str, key_id: int | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(message, key_id, retry_after)
        self.key_id = key_id
        self.retry_after = retry_after  # seconds until a request is counted again


class Unauthorized(AccessDenied):
    """No key, a key we never issued, or a revoked key."""


class Forbidden(AccessDenied):
    """A key whose scope doesn't cover the request, e.g. a `review` key on /v1."""


class RateLimited(AccessDenied):
    """The key made its `rate_per_min` requests in the last minute; `retry_after` says when one
    leaves it."""
