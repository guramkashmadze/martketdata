"""GET /health: process and database status for monitoring (PLAN.md §10.2).

Not part of /v1: it carries no data, so it has no envelope and no disclaimer (D-038).
"""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from marketdata.db.pool import Pool
from marketdata.service.status import DatabaseStatus, database_status

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok", "error"]
    db: DatabaseStatus


@router.get(
    "/health",
    response_model=Health,
    responses={503: {"model": Health, "description": "The database is unreachable."}},
)
async def get_health(request: Request) -> JSONResponse:
    pool: Pool = request.app.state.pool
    db = await database_status(pool)
    health = Health(status="ok" if db == "ok" else "error", db=db)
    return JSONResponse(health.model_dump(), status_code=200 if db == "ok" else 503)
