"""Earnings releases with proof: E7a's `marketdata earnings show`; the API serves them (E5a).

Reads earnings_releases and earnings_facts only (CLAUDE.md), and for releases that state no
period end the filings and metric rows of the quarter they were cross-checked with. Each value
carries its proof: the filing and exhibit on sec.gov, the row it was read from as printed, the
scale words, the checks it passed and its cross-check against the later 10-Q or 10-K (E4b,
D-028). A slot that failed, wasn't found or doesn't apply has no value, only why.

The API's release object (PLAN.md §10.2, D-097): the company, the period, the release and its
facts. The latest release of a company is its newest results release whatever its status, so
a release that failed verification is served with null values and their reasons rather than
an older one; the feed lists verified releases only (complete or partial), newest first.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import earnings, filings, financial_metrics, predecessors
from marketdata.db.repositories.earnings import StoredFact, StoredRelease
from marketdata.extract.interface import SLOTS
from marketdata.sec import urls
from marketdata.service.companies import (
    CompanyRef,
    company_refs,
    group_of,
    require,
    resolve_ticker,
)
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.pages import (
    BAD_CURSOR,
    DEFAULT_LIMIT,
    check_limit,
    decode_cursor,
    encode_cursor,
)
from marketdata.service.served import (
    FlagRef,
    Number,
    Served,
    Source,
    newest,
    sources_of,
    unique_flags,
)


class CrossCheck(BaseModel):
    """The slot against the later 10-Q or 10-K for its quarter (D-028, §9.10)."""

    model_config = ConfigDict(frozen=True)

    status: str  # pending, matched, mismatch, not_comparable
    filing_accession_no: str | None
    filing_value: Number | None


class FactView(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: str
    check_status: str  # passed, failed, not_found, not_applicable
    value: Decimal | None  # null unless passed
    prior_value: Decimal | None  # the prior-year quarter, from the same table
    change_pct: Decimal | None
    unit: str  # USD or USD/shares
    basis: str  # GAAP
    source_row_label: str | None
    source_quote: str | None  # the row as printed in the exhibit's text
    scale_text: str | None
    checks: tuple[str, ...]  # the checks it passed
    reasons: tuple[str, ...]  # why it failed, wasn't found or doesn't apply
    quality_flags: tuple[str, ...]
    method: str
    extractor_version: str
    cross_check: CrossCheck


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
    updated_at: datetime
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
        updated_at=release.updated_at,
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
        basis=fact.basis,
        source_row_label=fact.source_row_label,
        source_quote=fact.source_quote,
        scale_text=fact.scale_text,
        checks=tuple(entry.get("checks", [])),
        reasons=tuple(reasons),
        quality_flags=tuple(fact.quality_flags),
        method=fact.method,
        extractor_version=fact.extractor_version,
        cross_check=CrossCheck(
            status=fact.cross_check_status,
            filing_accession_no=fact.cross_check_accession,
            filing_value=fact.cross_check_value,
        ),
    )


# --- The API's release object (PLAN.md §10.2).


class ReleasePeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    fiscal_year: int | None  # D-008 labels
    fiscal_period: str | None
    period_end: date | None
    label: str | None  # the company's own wording, as printed
    # Where period_end comes from: the release, or, when it states none, the 10-Q or 10-K its
    # values were cross-checked with (period_end_accession_no); null when neither has it.
    period_end_source: Literal["release", "filing"] | None
    period_end_accession_no: str | None


class ReleaseInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: int  # the filer: a predecessor's CIK for releases filed before the company changed
    form: str | None  # 8-K or 8-K/A
    accepted_at: datetime | None  # when the SEC accepted the filing
    verified_at: datetime | None  # when it first served a verified value
    status: str  # complete, partial or failed
    filing_url: str  # the filing's index page on sec.gov
    document_url: str | None  # the press release exhibit
    extractor: str
    extractor_version: str
    notes: tuple[str, ...]


class FactProof(BaseModel):
    """Where an earnings value came from and the checks it passed (§9.10)."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    document_url: str | None
    source_quote: str | None  # the row as printed: label and cells
    scale_text: str | None  # e.g. "in millions, except per share amounts"
    checks: tuple[str, ...]  # in_source, scale, sanity: the checks it passed
    method: str  # rule
    extractor_version: str
    cross_check: CrossCheck


class EarningsFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: str  # revenue, operating_income, net_income, eps_diluted
    value: Number | None  # null unless it passed every check
    unit: str  # USD or USD/shares
    prior_value: Number | None  # the same quarter a year earlier, from the same table
    change_pct: Number | None  # percent, one decimal
    basis: str  # GAAP
    check_status: str  # passed, failed, not_found or not_applicable
    reasons: tuple[str, ...]  # why a value is null
    quality_flags: tuple[str, ...]
    proof: FactProof


class EarningsRelease(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str | None  # the ticker asked for, or the company's primary one
    cik: int  # the company's, also for a release its predecessor filed
    company: str | None
    period: ReleasePeriod
    release: ReleaseInfo
    facts: tuple[EarningsFact, ...]  # revenue, operating income, net income, diluted EPS


class EarningsFeed(BaseModel):
    model_config = ConfigDict(frozen=True)

    releases: tuple[EarningsRelease, ...]
    next_cursor: str | None  # pass it as `cursor` for the next page; null on the last


async def latest_earnings(conn: AsyncConnection[Any], ticker: str) -> Served[EarningsRelease]:
    """A company's latest results release (its predecessors' included), whatever its status:
    one that failed verification is served with null values and their reasons (D-097)."""
    security = await resolve_ticker(conn, ticker)
    require(security, "earnings")
    group = await group_of(conn, security.cik)
    stored = await earnings.latest_release(conn, group)
    if stored is None:
        raise NotFound(f"No earnings release of {security.ticker} is stored yet.")
    company = CompanyRef(ticker=security.ticker, cik=security.cik, name=security.name)
    return (await _build(conn, [stored], {stored.cik: company})).one()


async def get_earnings(conn: AsyncConnection[Any], accession_no: str) -> Served[EarningsRelease]:
    """One release by its accession number, whatever its status."""
    try:
        accession = urls.accession_no(accession_no)
    except ValueError as exc:
        raise InvalidParameter(str(exc)) from None
    stored = await earnings.get_release(conn, accession)
    if stored is None:
        raise NotFound(
            f"No earnings release has the accession number {accession}. Releases are 8-Ks with "
            "item 2.02 of covered companies; /v1/earnings lists them."
        )
    return (await _build(conn, [stored], await company_refs(conn, [stored.cik]))).one()


async def earnings_feed(
    conn: AsyncConnection[Any],
    *,
    since: date | datetime | None = None,
    ticker: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Served[EarningsFeed]:
    """Verified releases (complete or partial), newest first by acceptance time; of one company
    and its predecessors when `ticker` is given; accepted at or after `since` (a day means its
    start, UTC)."""
    check_limit(limit)
    after = None
    if cursor:
        accepted, accession = decode_cursor(cursor, 2)
        after = (_cursor_time(accepted), accession)
    ciks = None
    known: dict[int, CompanyRef] = {}
    if ticker is not None:
        security = await resolve_ticker(conn, ticker)
        require(security, "earnings")
        ciks = await group_of(conn, security.cik)
        known = dict.fromkeys(
            ciks, CompanyRef(ticker=security.ticker, cik=security.cik, name=security.name)
        )
    stored = await earnings.verified_page(
        conn, ciks, since=_start(since), after=after, limit=limit + 1
    )
    page = stored[:limit]
    companies = {**await company_refs(conn, {release.cik for release in page}), **known}
    built = await _build(conn, page, companies)
    last = page[-1] if len(stored) > limit else None
    next_cursor = (
        encode_cursor(last.accepted_at.isoformat(), last.accession_no)
        if last is not None and last.accepted_at is not None
        else None
    )
    return Served(
        data=EarningsFeed(releases=built.releases, next_cursor=next_cursor),
        sources=built.sources,
        as_of=built.as_of,
        quality_flags=built.quality_flags,
    )


@dataclass(frozen=True)
class _Built:
    releases: tuple[EarningsRelease, ...]
    sources: tuple[Source, ...]
    as_of: datetime | None
    quality_flags: tuple[FlagRef, ...]

    def one(self) -> Served[EarningsRelease]:
        (release,) = self.releases
        return Served(
            data=release, sources=self.sources, as_of=self.as_of, quality_flags=self.quality_flags
        )


async def _build(
    conn: AsyncConnection[Any], stored: Sequence[StoredRelease], companies: Mapping[int, CompanyRef]
) -> _Built:
    """The releases as the API serves them, with their sources, freshness and flags."""
    facts: dict[str, list[StoredFact]] = {}
    for fact in await earnings.facts_of(conn, [release.accession_no for release in stored]):
        facts.setdefault(fact.accession_no, []).append(fact)
    views = [release_view(release, facts.get(release.accession_no, [])) for release in stored]
    periods = await _periods(conn, views)
    releases = tuple(
        _release(view, companies[view.cik], periods[view.accession_no]) for view in views
    )
    return _Built(
        releases=releases,
        sources=await sources_of(conn, [ref for view in views for ref in _refs(view)]),
        as_of=newest(view.updated_at for view in views),
        quality_flags=unique_flags(flag for release in releases for flag in _flags(release)),
    )


async def _periods(
    conn: AsyncConnection[Any], views: Sequence[ReleaseView]
) -> dict[str, ReleasePeriod]:
    """Each release's period; a release that states no period end takes it from the 10-Q or
    10-K its values were cross-checked with, and its fiscal labels from that quarter's rows."""
    periods: dict[str, ReleasePeriod] = {}
    missing: dict[str, str] = {}  # release -> the filing it was cross-checked with
    for view in views:
        filing = _checked_filing(view.facts)
        if view.period_end is None and filing is not None:
            missing[view.accession_no] = filing
        periods[view.accession_no] = ReleasePeriod(
            fiscal_year=view.fiscal_year,
            fiscal_period=view.fiscal_period,
            period_end=view.period_end,
            label=view.period_label,
            period_end_source="release" if view.period_end is not None else None,
            period_end_accession_no=None,
        )
    if not missing:
        return periods
    ends = {
        listing.accession_no: listing.period_of_report
        for listing in await filings.listings(conn, sorted(set(missing.values())))
        if listing.period_of_report is not None
    }
    by_accession = {view.accession_no: view for view in views}
    groups = {accession: await group_of(conn, by_accession[accession].cik) for accession in missing}
    labels = await financial_metrics.fiscal_labels(
        conn,
        sorted({cik for group in groups.values() for cik in group}),
        sorted(set(ends.values())),
    )
    for accession, filing in missing.items():
        end = ends.get(filing)
        if end is None:
            continue
        label = next(
            (labels[(cik, end)] for cik in groups[accession] if (cik, end) in labels),
            (None, None),
        )
        periods[accession] = periods[accession].model_copy(
            update={
                "fiscal_year": label[0],
                "fiscal_period": label[1],
                "period_end": end,
                "period_end_source": "filing",
                "period_end_accession_no": filing,
            }
        )
    return periods


def _checked_filing(facts: Iterable[FactView]) -> str | None:
    """The 10-Q or 10-K the release's values were cross-checked with (the first slot's)."""
    return next(
        (
            fact.cross_check.filing_accession_no
            for fact in facts
            if fact.cross_check.filing_accession_no
        ),
        None,
    )


def _release(view: ReleaseView, company: CompanyRef, period: ReleasePeriod) -> EarningsRelease:
    return EarningsRelease(
        ticker=company.ticker,
        cik=company.cik,
        company=company.name,
        period=period,
        release=ReleaseInfo(
            accession_no=view.accession_no,
            cik=view.cik,
            form=view.form,
            accepted_at=view.accepted_at,
            verified_at=view.verified_at,
            status=view.status,
            filing_url=view.filing_url,
            document_url=view.document_url,
            extractor=view.extractor,
            extractor_version=view.extractor_version,
            notes=view.notes,
        ),
        facts=tuple(
            EarningsFact(
                slot=fact.slot,
                value=fact.value,
                unit=fact.unit,
                prior_value=fact.prior_value,
                change_pct=fact.change_pct,
                basis=fact.basis,
                check_status=fact.check_status,
                reasons=fact.reasons,
                quality_flags=fact.quality_flags,
                proof=FactProof(
                    accession_no=view.accession_no,
                    document_url=view.document_url,
                    source_quote=fact.source_quote,
                    scale_text=fact.scale_text,
                    checks=fact.checks,
                    method=fact.method,
                    extractor_version=fact.extractor_version,
                    cross_check=fact.cross_check,
                ),
            )
            for fact in view.facts
        ),
    )


def _refs(view: ReleaseView) -> list[tuple[int, str]]:
    """The 8-K and the filings its values were cross-checked with."""
    checked = {
        fact.cross_check.filing_accession_no
        for fact in view.facts
        if fact.cross_check.filing_accession_no
    }
    return [(view.cik, view.accession_no), *((view.cik, filing) for filing in sorted(checked))]


def _flags(release: EarningsRelease) -> list[FlagRef]:
    return [
        FlagRef(
            code=code,
            metric=fact.slot,
            period_end=release.period.period_end,
            ticker=release.ticker,
        )
        for fact in release.facts
        for code in fact.quality_flags
    ]


def _start(since: date | datetime | None) -> datetime | None:
    """A time without a zone is UTC; a day starts at midnight UTC."""
    if since is None:
        return None
    if isinstance(since, datetime):
        return since if since.tzinfo is not None else since.replace(tzinfo=UTC)
    return datetime.combine(since, time.min, tzinfo=UTC)


def _cursor_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise InvalidParameter(BAD_CURSOR)
    return moment
