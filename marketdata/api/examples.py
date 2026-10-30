"""Response examples for the API reference at /docs.

Apple's third quarter of fiscal 2026 and Alphabet's trailing twelve months as the dev database
served them on 2026-10-29, from their 8-K and 10-Q filings (shortened: two earnings slots, two
metrics). They are built from the response models, so they keep the shape the API returns.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from marketdata.sec import urls
from marketdata.service.changes import ChangeFeed, ValueChange
from marketdata.service.companies import Capability, CompanyRef, Coverage, CoveredTicker
from marketdata.service.compare import ComparedMetric, Comparison
from marketdata.service.earnings import (
    CrossCheck,
    EarningsFact,
    EarningsFeed,
    EarningsRelease,
    FactProof,
    ReleaseInfo,
    ReleasePeriod,
)
from marketdata.service.filings import CompanyFilings, FilingEventView, FilingView
from marketdata.service.financials import Financials, MetricProof, MetricView
from marketdata.service.json_text import dumps
from marketdata.service.pages import encode_cursor
from marketdata.service.served import DISCLAIMER, Served, Source
from marketdata.service.snapshot import CompanyIdentity, CompanySnapshot, LatestPeriod

AAPL, GOOGL = 320193, 1652044
RELEASE, TEN_Q = "0000320193-26-000018", "0000320193-26-000020"
EXHIBIT = "a8-kex991q3202606272026.htm"
SCALE = (
    "(In millions, except number of shares, which are reflected in thousands, and per-share "
    "amounts)"
)
AS_OF = datetime(2026, 10, 29, 17, 18, 56, tzinfo=UTC)
APPLE_TTM_SOURCES = (
    "0000320193-25-000079",
    "0000320193-26-000006",
    "0000320193-26-000013",
    TEN_Q,
)
GOOGL_TTM_SOURCES = (
    "0001652044-25-000091",
    "0001652044-26-000018",
    "0001652044-26-000048",
    "0001652044-26-000071",
)


def _source(cik: int, accession: str, form: str, filed: date) -> Source:
    return Source(
        accession_no=accession, form=form, filed_at=filed, url=urls.filing_folder(cik, accession)
    )


def _fact(slot: str, unit: str, values: tuple[str, str, str], quote: str) -> EarningsFact:
    value, prior, change = (Decimal(text) for text in values)
    return EarningsFact(
        slot=slot,
        value=value,
        unit=unit,
        prior_value=prior,
        change_pct=change,
        basis="GAAP",
        check_status="passed",
        reasons=(),
        quality_flags=(),
        proof=FactProof(
            accession_no=RELEASE,
            document_url=urls.filing_document(AAPL, RELEASE, EXHIBIT),
            source_quote=quote,
            scale_text=SCALE,
            checks=("in_source", "scale", "sanity"),
            method="rule",
            extractor_version="3",
            cross_check=CrossCheck(status="matched", filing_accession_no=TEN_Q, filing_value=value),
        ),
    )


APPLE_RELEASE = EarningsRelease(
    ticker="AAPL",
    cik=AAPL,
    company="Apple Inc.",
    period=ReleasePeriod(
        fiscal_year=2026,
        fiscal_period="Q3",
        period_end=date(2026, 6, 27),
        label="Three Months Ended June 27, 2026",
        period_end_source="release",
        period_end_accession_no=None,
    ),
    release=ReleaseInfo(
        accession_no=RELEASE,
        cik=AAPL,
        form="8-K",
        accepted_at=datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC),
        verified_at=datetime(2026, 10, 26, 18, 52, 41, tzinfo=UTC),
        status="complete",
        filing_url=urls.filing_index_page(AAPL, RELEASE),
        document_url=urls.filing_document(AAPL, RELEASE, EXHIBIT),
        extractor="rules",
        extractor_version="3",
        notes=(),
    ),
    facts=(
        _fact(
            "revenue",
            "USD",
            ("109417000000", "94036000000", "16.4"),
            "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |",
        ),
        _fact(
            "eps_diluted",
            "USD/shares",
            ("2.02", "1.57", "28.7"),
            "| Diluted | $ 2.02 | $ 1.57 | $ 6.88 | $ 5.62 |",
        ),
    ),
)
RELEASE_SOURCES = (
    _source(AAPL, TEN_Q, "10-Q", date(2026, 7, 31)),
    _source(AAPL, RELEASE, "8-K", date(2026, 7, 30)),
)


def _metric(
    cik: int,
    metric: str,
    period_type: str,
    period: tuple[int, str, date, date],
    value: str,
    method: str,
    concept: str,
    filing: tuple[str, date],
    sources: tuple[str, ...],
) -> MetricView:
    year, fiscal_period, start, end = period
    accession, filed = filing
    return MetricView(
        cik=cik,
        metric=metric,
        period_type=period_type,
        fiscal_year=year,
        fiscal_period=fiscal_period,
        period_start=start,
        period_end=end,
        value=Decimal(value),
        unit="USD",
        filed_at=filed,
        quality_flags=(),
        proof=MetricProof(
            accession_no=accession,
            filing_url=urls.filing_index_page(cik, accession),
            concept=concept,
            method=method,
            source_accessions=sources,
            calc_version="2.3",
        ),
    )


APPLE_Q3 = (2026, "Q3", date(2026, 3, 29), date(2026, 6, 27))
APPLE_TTM = (2026, "Q3", date(2025, 6, 29), date(2026, 6, 27))
APPLE_REVENUE_Q = _metric(
    AAPL,
    "revenue",
    "Q",
    APPLE_Q3,
    "109417000000",
    "reported",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    (TEN_Q, date(2026, 7, 31)),
    (TEN_Q,),
)
APPLE_NET_INCOME_Q = _metric(
    AAPL,
    "net_income",
    "Q",
    APPLE_Q3,
    "29789000000",
    "reported",
    "NetIncomeLoss",
    (TEN_Q, date(2026, 7, 31)),
    (TEN_Q,),
)
APPLE_REVENUE_TTM = _metric(
    AAPL,
    "revenue",
    "TTM",
    APPLE_TTM,
    "466823000000",
    "derived_ttm",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    (TEN_Q, date(2026, 7, 31)),
    APPLE_TTM_SOURCES,
)
APPLE_NET_INCOME_TTM = _metric(
    AAPL,
    "net_income",
    "TTM",
    APPLE_TTM,
    "128930000000",
    "derived_ttm",
    "NetIncomeLoss",
    (TEN_Q, date(2026, 7, 31)),
    APPLE_TTM_SOURCES,
)
GOOGL_REVENUE_TTM = _metric(
    GOOGL,
    "revenue",
    "TTM",
    (2026, "Q2", date(2025, 7, 1), date(2026, 6, 30)),
    "445867000000",
    "derived_ttm",
    "Revenues",
    ("0001652044-26-000071", date(2026, 7, 23)),
    GOOGL_TTM_SOURCES,
)
APPLE_TTM_FILINGS = (
    _source(AAPL, TEN_Q, "10-Q", date(2026, 7, 31)),
    _source(AAPL, "0000320193-26-000013", "10-Q", date(2026, 5, 1)),
    _source(AAPL, "0000320193-26-000006", "10-Q", date(2026, 1, 30)),
    _source(AAPL, "0000320193-25-000079", "10-K", date(2025, 10, 31)),
)

APPLE_FILINGS = (
    FilingView(
        accession_no=TEN_Q,
        cik=AAPL,
        form="10-Q",
        filed_at=date(2026, 7, 31),
        accepted_at=datetime(2026, 7, 31, 10, 1, 2, tzinfo=UTC),
        period_of_report=date(2026, 6, 27),
        url=urls.filing_index_page(AAPL, TEN_Q),
        events=(),
    ),
    FilingView(
        accession_no=RELEASE,
        cik=AAPL,
        form="8-K",
        filed_at=date(2026, 7, 30),
        accepted_at=datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC),
        period_of_report=date(2026, 7, 30),
        url=urls.filing_index_page(AAPL, RELEASE),
        events=(
            FilingEventView(
                item_or_form="2.02", label="Quarterly or annual results", importance="high"
            ),
        ),
    ),
)

SNAPSHOT: Served[CompanySnapshot] = Served(
    data=CompanySnapshot(
        company=CompanyIdentity(
            ticker="AAPL",
            tickers=("AAPL",),
            cik=AAPL,
            name="Apple Inc.",
            exchange="Nasdaq",
            filer_type="domestic",
            sic_code="3571",
            sic_description="Electronic Computers",
            fiscal_year_end="0926",
            state_of_inc="CA",
        ),
        latest_period=LatestPeriod(
            fiscal_year=2026, fiscal_period="Q3", period_end=date(2026, 6, 27)
        ),
        ttm=(APPLE_REVENUE_TTM, APPLE_NET_INCOME_TTM),
        not_reported=(),
        latest_release=APPLE_RELEASE,
        recent_filings=APPLE_FILINGS,
    ),
    sources=(*APPLE_TTM_FILINGS[:1], RELEASE_SOURCES[1], *APPLE_TTM_FILINGS[1:]),
    as_of=AS_OF,
    quality_flags=(),
)
EARNINGS_LATEST: Served[EarningsRelease] = Served(
    data=APPLE_RELEASE, sources=RELEASE_SOURCES, as_of=AS_OF, quality_flags=()
)
EARNINGS_FEED: Served[EarningsFeed] = Served(
    data=EarningsFeed(
        releases=(APPLE_RELEASE,),
        next_cursor=encode_cursor("2026-07-30T20:30:28+00:00", RELEASE),
    ),
    sources=RELEASE_SOURCES,
    as_of=AS_OF,
    quality_flags=(),
)
FINANCIALS: Served[Financials] = Served(
    data=Financials(
        ticker="AAPL",
        cik=AAPL,
        company="Apple Inc.",
        period_type="Q",
        metrics=("revenue", "net_income"),
        rows=(APPLE_REVENUE_Q, APPLE_NET_INCOME_Q),
    ),
    sources=RELEASE_SOURCES[:1],
    as_of=AS_OF,
    quality_flags=(),
)
FILINGS: Served[CompanyFilings] = Served(
    data=CompanyFilings(
        ticker="AAPL", cik=AAPL, company="Apple Inc.", filings=APPLE_FILINGS, next_cursor=None
    ),
    sources=RELEASE_SOURCES,
    as_of=AS_OF,
    quality_flags=(),
)
COMPARE: Served[Comparison] = Served(
    data=Comparison(
        period_type="TTM",
        companies=(
            CompanyRef(ticker="AAPL", cik=AAPL, name="Apple Inc."),
            CompanyRef(ticker="GOOGL", cik=GOOGL, name="Alphabet Inc."),
        ),
        metrics=(
            ComparedMetric(
                metric="revenue",
                unit="USD",
                values={"AAPL": APPLE_REVENUE_TTM, "GOOGL": GOOGL_REVENUE_TTM},
            ),
        ),
    ),
    sources=(
        _source(AAPL, TEN_Q, "10-Q", date(2026, 7, 31)),
        _source(GOOGL, "0001652044-26-000071", "10-Q", date(2026, 7, 23)),
    ),
    as_of=AS_OF,
    quality_flags=(),
)
_APPLIES = (
    Capability(
        kind="earnings",
        applies=True,
        reason="A domestic filer in the covered universe: its earnings releases apply.",
    ),
    Capability(
        kind="fundamentals",
        applies=True,
        reason="A domestic filer in the covered universe: its 10-K and 10-Q apply.",
    ),
)
COVERAGE: Served[Coverage] = Served(
    data=Coverage(
        tickers=(
            CoveredTicker(
                ticker="AAPL",
                security_type="common",
                cik=AAPL,
                name="Apple Inc.",
                filer_type="domestic",
                kinds=_APPLIES,
            ),
            CoveredTicker(
                ticker="SPY",
                security_type="common",
                cik=884394,
                name="SPDR S&P 500 ETF TRUST",
                filer_type="fund",
                kinds=(
                    Capability(
                        kind="earnings",
                        applies=False,
                        reason="A fund: earnings releases don't apply.",
                    ),
                    Capability(
                        kind="fundamentals",
                        applies=False,
                        reason="A fund: fundamentals don't apply.",
                    ),
                ),
            ),
        ),
        next_cursor=encode_cursor("SPY"),
    ),
    sources=(),
    as_of=AS_OF,
    quality_flags=(),
)


# E5b: Rivian's second-quarter 2026 revenue, which rules version 3 corrected: version 2 had
# read the six months as the quarter (EDGE_CASES.md).
RIVN, RIVN_RELEASE, RIVN_TEN_Q = 1874178, "0001874178-26-000053", "0001874178-26-000054"
RIVN_EXHIBIT = "ex-9912q26rivianearningspr.htm"
CHANGES: Served[ChangeFeed] = Served(
    data=ChangeFeed(
        changes=(
            ValueChange(
                change_id=27,
                changed_at=datetime(2026, 10, 28, 6, 22, 35, tzinfo=UTC),
                reason="correction",
                detail="rules version 2 to 3",
                entity="earnings_fact",
                ticker="RIVN",
                cik=RIVN,
                company="Rivian Automotive, Inc. / DE",
                filer_cik=RIVN,
                accession_no=RIVN_RELEASE,
                amends=None,
                slot="revenue",
                field="value",
                metric=None,
                period_type=None,
                period_end=date(2026, 6, 30),
                old_value=Decimal(3039000000),
                new_value=Decimal(1658000000),
                unit="USD",
                value=Decimal(1658000000),
                reasons=(),
                quality_flags=(),
                proof=FactProof(
                    accession_no=RIVN_RELEASE,
                    document_url=urls.filing_document(RIVN, RIVN_RELEASE, RIVN_EXHIBIT),
                    source_quote="| Total revenues | 1,303 | 1,658 | 2,543 | 3,039 |",
                    scale_text="(in millions, except per share amounts)",
                    checks=("in_source", "scale", "sanity"),
                    method="rule",
                    extractor_version="3",
                    cross_check=CrossCheck(
                        status="matched",
                        filing_accession_no=RIVN_TEN_Q,
                        filing_value=Decimal(1658000000),
                    ),
                ),
            ),
        ),
        next_cursor=encode_cursor("27"),
    ),
    sources=(
        _source(RIVN, RIVN_TEN_Q, "10-Q", date(2026, 7, 30)),
        _source(RIVN, RIVN_RELEASE, "8-K", date(2026, 7, 30)),
    ),
    as_of=datetime(2026, 10, 28, 6, 22, 35, tzinfo=UTC),
    quality_flags=(),
)


def ok(served: Served[Any], description: str) -> dict[int | str, dict[str, Any]]:
    """The 200 response of a route, with its example in the envelope."""
    body = {**served.model_dump(mode="python"), "disclaimer": DISCLAIMER}
    example = json.loads(dumps(body), parse_float=Decimal)
    return {
        200: {"description": description, "content": {"application/json": {"example": example}}}
    }


NOT_FOUND: dict[str, Any] = {
    "description": "No security has the ticker, or nothing is stored for it yet (`not_found`).",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "not_found",
                    "message": "No security has the ticker NOPE in the SEC's lists. Check the "
                    "spelling; share classes are written like BRK-B, BRK.B or BRK/B.",
                }
            }
        }
    },
}
NOT_APPLICABLE: dict[str, Any] = {
    "description": "The data doesn't apply to this filer class, e.g. a fund (`not_applicable`).",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "not_applicable",
                    "message": "SPY (SPDR S&P 500 ETF TRUST): A fund: fundamentals don't apply.",
                }
            }
        }
    },
}
