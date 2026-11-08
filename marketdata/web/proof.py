"""/proof: the proof page of a value (PLAN.md §9.10): a release or a metric value (E5b-S3), a
Form 4 (E15-S2) or a fund position (E15-S6), in the web UI's layout; every number in the UI
links to one (E15-S4).

The pages follow the web UI's access rule (web/access.py, §9.14): in private mode HTTP Basic
auth with an `api`-scope key as the password, each request logged and counted against the key's
rate limit like a /v1 request. Pages are HTML, not part of the API contract, so the API
reference leaves them out.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import HTMLResponse

from marketdata.api.deps import Conn
from marketdata.service.proof import (
    earnings_proof,
    fund_position_proof,
    insider_filing_proof,
    metric_proof,
    stake_filing_proof,
)
from marketdata.web.access import require_ui_access
from marketdata.web.render import page

router = APIRouter(
    prefix="/proof", include_in_schema=False, dependencies=[Depends(require_ui_access)]
)


@router.get("/earnings/{accession_no}", response_class=HTMLResponse)
async def earnings_page(
    conn: Conn, accession_no: Annotated[str, Path(description="The 8-K's accession number.")]
) -> HTMLResponse:
    proof = await earnings_proof(conn, accession_no)
    release = proof.release
    return page(
        "proof/earnings.html",
        proof=proof,
        r=release,
        who=release.company or f"CIK {release.cik}",
    )


@router.get("/metrics/{ticker}/{metric}/{period_type}/{period_end}", response_class=HTMLResponse)
async def metric_page(
    conn: Conn, ticker: str, metric: str, period_type: str, period_end: str
) -> HTMLResponse:
    proof = await metric_proof(conn, ticker, metric, period_type, period_end)
    return page(
        "proof/metric.html",
        proof=proof,
        row=proof.row,
        who=proof.company or f"CIK {proof.cik}",
    )


@router.get("/insiders/{accession_no}", response_class=HTMLResponse)
async def insider_page(
    conn: Conn, accession_no: Annotated[str, Path(description="The Form 4's accession number.")]
) -> HTMLResponse:
    return page("proof/insider.html", proof=await insider_filing_proof(conn, accession_no))


@router.get("/stakes/{accession_no}", response_class=HTMLResponse)
async def stake_page(
    conn: Conn,
    accession_no: Annotated[str, Path(description="The Schedule 13D or 13G's accession number.")],
) -> HTMLResponse:
    return page("proof/stake.html", proof=await stake_filing_proof(conn, accession_no))


@router.get("/funds/{manager_cik}/{quarter}/{cusip}", response_class=HTMLResponse)
async def position_page(
    conn: Conn,
    manager_cik: int,
    quarter: str,
    cusip: str,
    put_call: Annotated[str | None, Query(description="PUT or CALL for an option.")] = None,
) -> HTMLResponse:
    proof = await fund_position_proof(conn, manager_cik, quarter, cusip, put_call)
    return page("proof/position.html", proof=proof)
