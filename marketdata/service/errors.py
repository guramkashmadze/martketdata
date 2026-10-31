"""Domain exceptions raised by service functions (PLAN.md §6), and the error code and status
each is answered with (§10.1): InvalidParameter 400, Unauthorized 401, Forbidden 403, NotFound
404, NotApplicable 422, RateLimited 429. The API's error envelope (api/errors.py) and the MCP
server's tool errors and request log (mcp_server/, E6) both use `answer_of`. Messages say how
to fix the request where possible."""


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


# Most specific first: RateLimited and the other access errors share a base class.
ANSWERS: tuple[tuple[type[DomainError], int, str], ...] = (
    (InvalidParameter, 400, "invalid_parameter"),
    (Unauthorized, 401, "unauthorized"),
    (Forbidden, 403, "forbidden"),
    (NotFound, 404, "not_found"),
    (NotApplicable, 422, "not_applicable"),
    (RateLimited, 429, "rate_limited"),
)

INTERNAL = (500, "internal")
INTERNAL_MESSAGE = (
    "Something went wrong on our side. Try again later; if it keeps failing, tell us the time "
    "of the request."
)


def answer_of(exc: DomainError) -> tuple[int, str]:
    """The status and error code a domain error is answered with; 500 `internal` for a domain
    error no row names."""
    for kind, status, code in ANSWERS:
        if isinstance(exc, kind):
            return status, code
    return INTERNAL
