"""A database connection per request.

`app.state.connect` hands out connections: the pool's in a running app, the test's connection
in the tests. Endpoints take `Conn`, which returns the connection when the endpoint returns,
before the response is sent; the API only reads, apart from the request log
(api/request_log.py), which takes a connection of its own.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any

from fastapi import Depends, Request
from psycopg import AsyncConnection

# A pool's `connection` method: a connection whose transaction commits on exit.
type Connect = Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]


# FastAPI runs a dependency's generator as a context manager, so the connection is returned
# however the request ends.
async def connection(request: Request) -> AsyncIterator[AsyncConnection[Any]]:
    connect: Connect = request.app.state.connect
    async with connect() as conn:
        yield conn  # noqa: ASYNC119


Conn = Annotated[AsyncConnection[Any], Depends(connection, scope="function")]
