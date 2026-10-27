"""Earnings releases with proof: E7a's `marketdata earnings show`; E5a serves them.

Reads earnings_releases and earnings_facts only (CLAUDE.md). Each value carries its proof: the
filing and exhibit on sec.gov, the row it was read from as printed, the scale words and the
checks it passed. A slot that failed, wasn't found or doesn't apply has no value, only why.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import earnings, predecessors
from marketdata.db.repositories.earnings import StoredFact, StoredRelease
from marketdata.extract.interface import SLOTS
from marketdata.sec import urls
from marketdata.service.errors import NotFound


class FactView(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: str
    check_status: str  # passed, failed, not_found, not_applicable
    value: Decimal | None  # null unless passed
    prior_value: Decimal | None  # the prior-year quarter, from the same table
    change_pct: Decimal | None
    unit: str  # USD or USD/shares
    source_row_label: str | None
    source_quote: str | None  # the row as printed in the exhibit's text
    scale_text: str | None
    checks: tuple[str, ...]  # the checks it passed
    reasons: tuple[str, ...]  # why it failed, wasn't found or doesn't apply
    quality_flags: tuple[str, ...]
    method: str
    extractor_version: str


class ReleaseView(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: int  # the filer: a predecessor's CIK for releases filed before the company changed
    form: str | None
    accepted_at: datetime | None
    period_end: date | None  # null when no exhibit states the quarter's end date
    period_label: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    status: str  # complete, partial, failed
    extractor: str
    extractor_version: str
    verified_at: datetime | None
    filing_url: str  # the filing's index page on sec.gov
    document_url: str | None  # the exhibit the values were read from
    notes: tuple[str, ...]
    facts: tuple[FactView, ...]  # SLOTS order


async def latest_release(conn: AsyncConnection[Any], cik: int) -> ReleaseView:
    """The company's latest results release, its predecessors' included (data/predecessors.csv).

    Raises NotFound when none is stored."""
    ciks = [cik, *await predecessors.predecessors_of(conn, cik)]
    release = await earnings.latest_release(conn, ciks)
    if release is None:
        raise NotFound(
            f"No earnings release of CIK {cik} is stored. `marketdata backfill earnings` and the "
            "watcher load them."
        )
    return release_view(release, await earnings.facts(conn, release.accession_no))


def release_view(release: StoredRelease, facts: list[StoredFact]) -> ReleaseView:
    report = release.check_report
    order: dict[str, int] = {slot: position for position, slot in enumerate(SLOTS)}
    return ReleaseView(
        accession_no=release.accession_no,
        cik=release.cik,
        form=report.get("form"),
        accepted_at=release.accepted_at,
        period_end=release.period_end,
        period_label=release.period_label,
        fiscal_year=release.fiscal_year,
        fiscal_period=release.fiscal_period,
        status=release.status,
        extractor=release.extractor,
        extractor_version=release.extractor_version,
        verified_at=release.verified_at,
        filing_url=urls.filing_index_page(release.cik, release.accession_no),
        document_url=(
            urls.filing_document(release.cik, release.accession_no, release.doc_name)
            if release.doc_name
            else None
        ),
        notes=tuple(report.get("notes", [])),
        facts=tuple(
            _fact_view(fact, report.get("slots", {}).get(fact.slot, {}))
            for fact in sorted(facts, key=lambda fact: order.get(fact.slot, len(order)))
        ),
    )


def _fact_view(fact: StoredFact, entry: dict[str, Any]) -> FactView:
    reasons = [*entry.get("problems", [])]
    if fact.check_status != "passed" and entry.get("note"):
        reasons.append(entry["note"])
    return FactView(
        slot=fact.slot,
        check_status=fact.check_status,
        value=fact.value,
        prior_value=fact.prior_value,
        change_pct=fact.change_pct,
        unit=fact.unit,
        source_row_label=fact.source_row_label,
        source_quote=fact.source_quote,
        scale_text=fact.scale_text,
        checks=tuple(entry.get("checks", [])),
        reasons=tuple(reasons),
        quality_flags=tuple(fact.quality_flags),
        method=fact.method,
        extractor_version=fact.extractor_version,
    )
