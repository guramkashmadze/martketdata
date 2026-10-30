"""/proof: the proof page of a release or a metric value (E5b-S3, PLAN.md §9.10).

In M1 a page needs HTTP Basic auth with an `api`-scope key as the password (the user name is
ignored), over TLS; from E15 the pages follow the web UI's access rule (§9.14). Each request is
logged and counted against the key's rate limit like a /v1 request. Pages are HTML, not part
of the API contract, so the API reference leaves them out.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import HTMLResponse

from marketdata.api.auth import require_proof_key
from marketdata.api.deps import Conn
from marketdata.api.proof_pages.render import page
from marketdata.service.proof import earnings_proof, metric_proof

router = APIRouter(
    prefix="/proof", include_in_schema=False, dependencies=[Depends(require_proof_key)]
)


@router.get("/earnings/{accession_no}", response_class=HTMLResponse)
async def earnings_page(
    conn: Conn, accession_no: Annotated[str, Path(description="The 8-K's accession number.")]
) -> HTMLResponse:
    proof = await earnings_proof(conn, accession_no)
    release = proof.release
    return page(
        "earnings.html",
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
        "metric.html",
        proof=proof,
        row=proof.row,
        who=proof.company or f"CIK {proof.cik}",
    )
