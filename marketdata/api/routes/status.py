"""/v1/status: how fresh the data is and the published accuracy report (E5b-S4, PLAN.md
§9.10, §10.2). It needs no key (D-095), so it sits outside the keyed /v1 router; it is still
logged, and an answer is reused for a minute."""

import time

from fastapi import APIRouter, Request

from marketdata.api import examples
from marketdata.api.deps import Conn
from marketdata.api.envelope import Envelope, EnvelopeResponse, respond
from marketdata.service.served import Served
from marketdata.service.status import StatusReport, status_report

CACHE_SECONDS = 60.0

router = APIRouter(prefix="/v1", tags=["Status"])


@router.get(
    "/status",
    summary="Freshness and accuracy",
    response_model=Envelope[StatusReport],
    response_class=EnvelopeResponse,
    responses=examples.ok(examples.STATUS, "How fresh the data is and how accurate."),
)
async def status(request: Request, conn: Conn) -> EnvelopeResponse:
    """The newest release, metric row, filing and watch, and the accuracy report: the golden-set
    match rate per slot and metric, the cross-check match rate against the 10-Q and 10-K, the
    extraction pass rate, the minutes from SEC acceptance to verified for releases processed
    live, and value changes by reason over 90 days. No key is needed."""
    cached: tuple[float, Served[StatusReport]] | None = getattr(
        request.app.state, "status_cache", None
    )
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        return respond(cached[1])
    served = await status_report(conn)
    request.app.state.status_cache = (time.monotonic(), served)
    return respond(served)
