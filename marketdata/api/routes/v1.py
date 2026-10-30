"""The /v1 data routes (PLAN.md §10.2). Each needs an `api`-scope key: the router depends on
`require_api_key`, so no route can be added without it. /v1/status, which needs no key, will
have a router of its own (E7c, E5b)."""

from fastapi import APIRouter, Depends

from marketdata.api.auth import require_api_key
from marketdata.api.errors import ERROR_RESPONSES
from marketdata.api.routes import changes, companies, compare, coverage, earnings

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)], responses=ERROR_RESPONSES)
router.include_router(coverage.router)
router.include_router(companies.router)
router.include_router(earnings.router)
router.include_router(compare.router)
router.include_router(changes.router)
