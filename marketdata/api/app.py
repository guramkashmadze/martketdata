"""The FastAPI app: /health now; /v1, /proof, /mcp and the web UI are mounted by later epics.

Run with `pdm run uvicorn marketdata.api.app:app`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from marketdata.api.routes import health
from marketdata.config import Settings, load_settings
from marketdata.db.pool import create_pool
from marketdata.log import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.env, settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Opens without waiting for the database (see db/pool.py) and closes on shutdown.
        async with create_pool(str(settings.database_url)) as pool:
            app.state.pool = pool
            yield

    app = FastAPI(title="marketdata", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
