"""Domain exceptions and HTTP errors to the error envelope (PLAN.md §10.1, E5a-S1).

`{"error": {"code": ..., "message": ...}}` with invalid_parameter (400), unauthorized (401),
forbidden (403), not_found (404), method_not_allowed (405), not_applicable (422), rate_limited
(429, with Retry-After) and internal (500). FastAPI answers a parameter that fails validation
with 422; here it is 400 invalid_parameter, so 422 always means not_applicable (D-096).
"""

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from marketdata.service.errors import (
    DomainError,
    Forbidden,
    InvalidParameter,
    NotApplicable,
    NotFound,
    RateLimited,
    Unauthorized,
)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


# Most specific first: RateLimited and the other access errors share a base class.
_DOMAIN: Sequence[tuple[type[DomainError], int, str]] = (
    (InvalidParameter, 400, "invalid_parameter"),
    (Unauthorized, 401, "unauthorized"),
    (Forbidden, 403, "forbidden"),
    (NotFound, 404, "not_found"),
    (NotApplicable, 422, "not_applicable"),
    (RateLimited, 429, "rate_limited"),
)

INTERNAL_MESSAGE = (
    "Something went wrong on our side. Try again later; if it keeps failing, tell us the time "
    "of the request."
)

# For the OpenAPI document: the errors every /v1 data route can answer.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "A parameter is invalid (`invalid_parameter`)."},
    401: {
        "model": ErrorEnvelope,
        "description": "No key, an unknown key or a revoked key (`unauthorized`).",
    },
    403: {"model": ErrorEnvelope, "description": "The key's scope isn't `api` (`forbidden`)."},
    429: {
        "model": ErrorEnvelope,
        "description": "Over the key's requests per minute (`rate_limited`); see Retry-After.",
    },
}


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


async def domain_error(request: Request, exc: Exception) -> JSONResponse:
    for kind, status, code in _DOMAIN:
        if isinstance(exc, kind):
            retry_after = exc.retry_after if isinstance(exc, RateLimited) else None
            headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
            return error_response(status, code, str(exc), headers)
    return await internal_error(request, exc)


async def validation_error(  # noqa: ASYNC124 - Starlette awaits exception handlers
    request: Request, exc: Exception
) -> JSONResponse:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    problems = [f"{_where(error)}: {error.get('msg', 'invalid')}" for error in errors]
    return error_response(400, "invalid_parameter", "; ".join(problems) or "Invalid request.")


async def http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):
        return await internal_error(request, exc)
    path = request.url.path
    if exc.status_code == 404:
        return error_response(404, "not_found", f"There is no endpoint {path}; /docs lists them.")
    if exc.status_code == 405:
        allow = (exc.headers or {}).get("Allow", "GET")
        return error_response(
            405, "method_not_allowed", f"{path} answers {allow} only.", {"Allow": allow}
        )
    code = "internal" if exc.status_code >= 500 else "invalid_parameter"
    return error_response(exc.status_code, code, str(exc.detail))


async def internal_error(  # noqa: ASYNC124 - Starlette awaits exception handlers
    request: Request, exc: Exception
) -> JSONResponse:
    # The server logs the exception with its traceback; the client gets no internals.
    return error_response(500, "internal", INTERNAL_MESSAGE)


def install(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, internal_error)


def _where(error: Any) -> str:
    # ('query', 'limit') -> 'limit'; a body or header location keeps its last part.
    location = [str(part) for part in error.get("loc", ()) if part not in ("query", "path")]
    return ".".join(location) or "request"
