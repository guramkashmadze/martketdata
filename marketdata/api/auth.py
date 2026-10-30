"""X-API-Key auth and key scopes (E5a-S1, PLAN.md §10.1).

Every /v1 data route depends on `require_api_key` through its router (api/routes/v1.py), so a
route can't be added without it; /v1/status (E5b) is outside that router. Proof pages (E5b-S3)
take the same `api`-scope key as the password of HTTP Basic auth (`require_proof_key`), with
any user name. The key's id goes to the request log, also when the key is refused.
"""

from typing import Annotated

from fastapi import Request, Security
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

from marketdata.api.deps import Conn
from marketdata.service.clients import Caller, authenticate
from marketdata.service.errors import AccessDenied, Unauthorized

api_key_header = APIKeyHeader(
    name="X-API-Key",
    description="Your API key, with the `api` scope. The operator issues it; it is shown once.",
    auto_error=False,  # a missing key is answered by the error envelope, not FastAPI's 403
)


PROOF_REALM = "marketdata proof pages"
proof_credentials = HTTPBasic(realm=PROOF_REALM, auto_error=False)
SIGN_IN = (
    "This page needs your API key: sign in with any user name and the key as the password. "
    "Ask the operator for a key."
)


async def require_api_key(
    request: Request, conn: Conn, key: Annotated[str | None, Security(api_key_header)]
) -> Caller:
    return await _caller(request, conn, key)


async def require_proof_key(
    request: Request,
    conn: Conn,
    credentials: Annotated[HTTPBasicCredentials | None, Security(proof_credentials)],
) -> Caller:
    if credentials is None or not credentials.password.strip():
        raise Unauthorized(SIGN_IN)
    return await _caller(request, conn, credentials.password)


async def _caller(request: Request, conn: Conn, key: str | None) -> Caller:
    try:
        caller = await authenticate(conn, key, scope="api")
    except AccessDenied as exc:
        request.state.key_id = exc.key_id
        raise
    request.state.key_id = caller.key_id
    return caller
