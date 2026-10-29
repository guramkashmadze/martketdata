"""The FastAPI app: /health, the /v1 data API (E5a) with its reference at /docs; /proof and /mcp
and the web UI are mounted by later epics.

Run with `pdm run uvicorn marketdata.api.app:app`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from marketdata.api import errors
from marketdata.api.deps import Connect
from marketdata.api.request_log import RequestLog
from marketdata.api.routes import health, v1
from marketdata.config import Settings, load_settings
from marketdata.db.pool import create_pool
from marketdata.log import configure_logging

DESCRIPTION = """
Verified data from SEC filings: earnings releases, financial metrics and filings of US public
companies. Every value carries its proof: the filing it came from, the exact source, the checks
it passed and, for earnings releases, the cross-check against the later 10-Q or 10-K.

Send your API key in the `X-API-Key` header. Every successful response has the same envelope:
`data`, `sources` (the filings used), `as_of`, `quality_flags` and the disclaimer. Missing or
unverified values are `null` with the reason, never an estimate.

Information from public filings. Not investment advice.
"""


def create_app(settings: Settings | None = None, *, connect: Connect | None = None) -> FastAPI:
    """The app; `connect` replaces the pool's connections (the tests pass their own)."""
    settings = settings or load_settings()
    configure_logging(settings.env, settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Opens without waiting for the database (see db/pool.py) and closes on shutdown.
        async with create_pool(str(settings.database_url)) as pool:
            app.state.pool = pool
            if connect is None:
                app.state.connect = pool.connection
            yield

    app = FastAPI(
        title="marketdata API",
        version="1",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    if connect is not None:
        app.state.connect = connect
    app.include_router(health.router)
    app.include_router(v1.router)
    errors.install(app)
    app.add_middleware(RequestLog)
    return app


app = create_app()
