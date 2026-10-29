"""X-API-Key auth and key scopes (E5a-S1, PLAN.md §10.1).

Every /v1 data route depends on `require_api_key` through its router (api/routes/v1.py), so a
route can't be added without it; /v1/status (E7c, E5b) will be outside that router. The key's
id goes to the request log, also when the key is refused.
"""

from typing import Annotated

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from marketdata.api.deps import Conn
from marketdata.service.clients import Caller, authenticate
from marketdata.service.errors import AccessDenied

api_key_header = APIKeyHeader(
    name="X-API-Key",
    description="Your API key, with the `api` scope. The operator issues it; it is shown once.",
    auto_error=False,  # a missing key is answered by the error envelope, not FastAPI's 403
)


async def require_api_key(
    request: Request, conn: Conn, key: Annotated[str | None, Security(api_key_header)]
) -> Caller:
    try:
        caller = await authenticate(conn, key, scope="api")
    except AccessDenied as exc:
        request.state.key_id = exc.key_id
        raise
    request.state.key_id = caller.key_id
    return caller
