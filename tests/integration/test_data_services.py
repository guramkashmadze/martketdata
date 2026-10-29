"""E5a-S2: the data service functions on seeded PostgreSQL (tests/integration/api_seed.py). Each
returns its data with the filings it came from, how fresh it is and the quality flags on its
values; every value carries its proof (§9.10)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from api_seed import (
    ACME,
    FY2020_10K,
    OLD,
    OLD_Q1_10Q,
    OLD_Q1_RELEASE,
    PROSPECTUS,
    Q2_10Q,
    Q2_RELEASE,
    Q3_RELEASE,
    seed,
)

from marketdata.sec import urls
from marketdata.service.companies import coverage
from marketdata.service.compare import compare
from marketdata.service.earnings import earnings_feed, get_earnings, latest_earnings
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.filings import recent_filings
from marketdata.service.financials import CORE_METRICS, get_financials
from marketdata.service.served import FlagRef
from marketdata.service.snapshot import company_snapshot

Conn = psycopg.AsyncConnection[Any]
TODAY = date(2026, 10, 29)


@pytest.fixture
async def seeded(conn: Conn) -> Conn:
    await seed(conn)
    return conn


async def test_coverage_lists_universe_tickers_with_their_class_and_kinds(seeded: Conn) -> None:
    first = await coverage(seeded, limit=3)
    rest = await coverage(seeded, limit=3, cursor=first.data.next_cursor)

    listed = [*first.data.tickers, *rest.data.tickers]
    assert [t.ticker for t in listed] == ["ACME", "ACME-PA", "BETA", "FNDX", "FPIX"]
    assert rest.data.next_cursor is None  # OUTC is outside the universe
    fund = listed[3]
    assert (fund.filer_type, [(k.kind, k.applies) for k in fund.kinds]) == (
        "fund",
        [("earnings", False), ("fundamentals", False)],
    )
    assert fund.kinds[1].reason == "A fund: fundamentals don't apply."
    assert all(kind.applies for kind in listed[0].kinds)
    assert first.sources == () and first.as_of is not None
    with pytest.raises(InvalidParameter, match="cursor"):
        await coverage(seeded, cursor="garbage")
    with pytest.raises(InvalidParameter, match="limit must be from 1 to 100"):
        await coverage(seeded, limit=0)


async def test_the_latest_release_is_served_even_when_it_failed(seeded: Conn) -> None:
    served = await latest_earnings(seeded, "acme")

    release = served.data
    assert (release.ticker, release.cik, release.company) == ("ACME", ACME, "Acme Corp")
    assert (release.release.accession_no, release.release.status) == (Q3_RELEASE, "failed")
    assert release.release.notes == ("the exhibit is printed as pictures",)
    [revenue] = release.facts
    assert (revenue.value, revenue.check_status, revenue.reasons) == (
        None,
        "failed",
        ("not in the source",),
    )
    assert revenue.proof.accession_no == Q3_RELEASE
    assert revenue.proof.document_url == urls.filing_document(ACME, Q3_RELEASE, "ex99.htm")
    assert [source.accession_no for source in served.sources] == [Q3_RELEASE]
    assert served.as_of is not None and served.as_of.tzinfo == UTC


async def test_a_release_without_a_stated_period_end_takes_it_from_the_filing(
    seeded: Conn,
) -> None:
    served = await get_earnings(seeded, Q2_RELEASE.replace("-", ""))

    release = served.data
    assert release.period.model_dump() == {
        "fiscal_year": 2026,
        "fiscal_period": "Q2",
        "period_end": date(2026, 6, 30),
        "label": "Second Quarter 2026",
        "period_end_source": "filing",
        "period_end_accession_no": Q2_10Q,
    }
    revenue, eps = release.facts
    assert (revenue.value, revenue.prior_value, revenue.change_pct) == (
        Decimal(1000),
        Decimal(950),
        Decimal("5.3"),
    )
    assert revenue.proof.checks == ("in_source", "scale", "sanity")
    assert revenue.proof.source_quote == "Total revenues | 950 | 1000"
    assert revenue.proof.cross_check.model_dump() == {
        "status": "matched",
        "filing_accession_no": Q2_10Q,
        "filing_value": Decimal(1000),
    }
    assert eps.proof.cross_check.status == "mismatch"
    # The 8-K and the 10-Q its values were checked with, newest first.
    assert [(s.accession_no, s.form) for s in served.sources] == [
        (Q2_10Q, "10-Q"),
        (Q2_RELEASE, "8-K"),
    ]
    assert served.sources[1].url == urls.filing_folder(ACME, Q2_RELEASE)
    assert served.quality_flags == (
        FlagRef(
            code="release_vs_filing_mismatch",
            metric="eps_diluted",
            period_end=date(2026, 6, 30),
            ticker="ACME",
        ),
    )


async def test_a_predecessors_release_belongs_to_the_company_now(seeded: Conn) -> None:
    release = (await get_earnings(seeded, OLD_Q1_RELEASE)).data

    assert (release.ticker, release.cik, release.release.cik) == ("ACME", ACME, OLD)
    assert release.period.period_end_source == "release"
    with pytest.raises(NotFound, match="0001001000-26-999999"):
        await get_earnings(seeded, "0001001000-26-999999")
    with pytest.raises(InvalidParameter, match="18 digits"):
        await get_earnings(seeded, "not-an-accession")


async def test_the_feed_lists_verified_releases_newest_first(seeded: Conn) -> None:
    every = (await earnings_feed(seeded)).data
    acme = (await earnings_feed(seeded, ticker="ACME")).data
    since = (await earnings_feed(seeded, since=date(2026, 5, 1))).data
    first = (await earnings_feed(seeded, limit=1)).data
    second = (await earnings_feed(seeded, limit=1, cursor=first.next_cursor)).data

    # The failed third quarter is left out; the predecessor's release is ACME's.
    assert [r.release.accession_no for r in every.releases] == [Q2_RELEASE, OLD_Q1_RELEASE]
    assert [r.release.accession_no for r in acme.releases] == [Q2_RELEASE, OLD_Q1_RELEASE]
    assert {r.ticker for r in every.releases} == {"ACME"}
    assert [r.release.accession_no for r in since.releases] == [Q2_RELEASE]
    assert [r.release.accession_no for r in second.releases] == [OLD_Q1_RELEASE]
    assert second.next_cursor is None
    since_moment = datetime(2026, 7, 28, 20, 5, tzinfo=UTC)
    assert (await earnings_feed(seeded, since=since_moment)).data.releases[0].ticker == "ACME"
    assert (await earnings_feed(seeded, ticker="BETA")).data.releases == ()


async def test_financials_merge_the_predecessor_and_carry_proof(seeded: Conn) -> None:
    served = await get_financials(seeded, "ACME", metrics=["revenue"], period="Q", today=TODAY)

    data = served.data
    assert (data.ticker, data.period_type, data.metrics) == ("ACME", "Q", ("revenue",))
    assert [(r.period_end, r.value, r.cik) for r in data.rows] == [
        (date(2026, 6, 30), Decimal(1000), ACME),
        (date(2026, 3, 31), Decimal(900), OLD),
    ]
    proof = data.rows[0].proof
    assert (proof.accession_no, proof.concept, proof.method, proof.calc_version) == (
        Q2_10Q,
        "Revenues",
        "reported",
        "2.3",
    )
    assert proof.filing_url == urls.filing_index_page(ACME, Q2_10Q)
    assert [s.accession_no for s in served.sources] == [Q2_10Q, OLD_Q1_10Q]
    assert served.sources[1].url == urls.filing_folder(OLD, OLD_Q1_10Q)
    assert served.quality_flags == ()


async def test_a_filing_older_than_the_filings_backfill_is_found_through_its_facts(
    seeded: Conn,
) -> None:
    served = await get_financials(seeded, "ACME", metrics=["revenue"], period="FY", today=TODAY)

    [source] = served.sources
    assert (source.accession_no, source.form, source.filed_at) == (
        FY2020_10K,
        "10-K",
        date(2021, 2, 20),
    )


async def test_financials_default_to_the_core_set_and_flag_stale_data(seeded: Conn) -> None:
    later = date(2027, 1, 1)  # the latest filing is then more than 120 days old
    served = await get_financials(seeded, "ACME", period="TTM", today=later)

    assert served.data.metrics == CORE_METRICS
    by_metric = {row.metric: row for row in served.data.rows}
    assert set(by_metric) == {"revenue", "net_income", "operating_cash_flow", "net_margin"}
    gap = by_metric["operating_cash_flow"]
    assert (gap.value, gap.quality_flags) == (None, ("ttm_gap", "stale_data"))
    assert by_metric["net_margin"].unit == "percent"
    assert by_metric["net_margin"].proof.concept is None  # computed
    codes = {(flag.code, flag.metric) for flag in served.quality_flags}
    assert ("ttm_gap", "operating_cash_flow") in codes
    assert ("stale_data", "revenue") in codes
    with pytest.raises(InvalidParameter, match="unknown metric 'ebitda'"):
        await get_financials(seeded, "ACME", metrics=["ebitda"])
    with pytest.raises(InvalidParameter, match="limit must be from 1 to 40"):
        await get_financials(seeded, "ACME", limit=41)


async def test_the_snapshot(seeded: Conn) -> None:
    served = await company_snapshot(seeded, "ACME", today=TODAY)

    snapshot = served.data
    assert snapshot.company.tickers == ("ACME", "ACME-PA")
    assert (snapshot.company.name, snapshot.company.filer_type) == ("Acme Corp", "domestic")
    assert snapshot.latest_period is not None
    assert (snapshot.latest_period.fiscal_period, snapshot.latest_period.period_end) == (
        "Q2",
        date(2026, 6, 30),
    )
    assert [row.metric for row in snapshot.ttm] == [
        "revenue",
        "net_income",
        "operating_cash_flow",
        "net_margin",
    ]
    assert "long_term_debt" in snapshot.not_reported
    assert snapshot.latest_release is not None
    assert snapshot.latest_release.release.accession_no == Q3_RELEASE
    # Results, reports and current reports; the 424B2 prospectus is left out.
    assert [f.accession_no for f in snapshot.recent_filings] == [Q3_RELEASE, Q2_10Q, Q2_RELEASE]
    assert PROSPECTUS not in {source.accession_no for source in served.sources}
    assert {Q2_10Q, OLD_Q1_10Q, Q3_RELEASE} <= {s.accession_no for s in served.sources}


async def test_the_snapshot_shows_stakes_in_the_company_only(seeded: Conn) -> None:
    # ACME's own stake in another company (no 005- file number, D-061), a 13D about ACME and a
    # 13G amendment about ACME, which §9.6 hides.
    own, about, amended = "0001001000-26-000050", "0000999999-26-000001", "0000999999-26-000002"
    await seeded.execute(
        "insert into filings (accession_no, cik, form, filed_at, accepted_at, file_number) "
        "values (%s, %s, 'SCHEDULE 13G/A', '2026-10-29', '2026-10-29T12:00:00Z', null), "
        "(%s, %s, 'SCHEDULE 13D', '2026-10-29', '2026-10-29T13:00:00Z', '005-12345'), "
        "(%s, %s, 'SCHEDULE 13G/A', '2026-10-29', '2026-10-29T14:00:00Z', '005-12345')",
        [own, ACME, about, ACME, amended, ACME],
    )

    snapshot = (await company_snapshot(seeded, "ACME", today=TODAY)).data

    assert [f.accession_no for f in snapshot.recent_filings] == [about, Q3_RELEASE, Q2_10Q]
    assert [e.label for e in snapshot.recent_filings[0].events] == [
        "An investor crossed 5% and may seek to influence the company"
    ]
    every = (await recent_filings(seeded, "ACME", limit=3)).data.filings
    assert [f.accession_no for f in every] == [amended, about, own]  # the filings list keeps all


async def test_compare(seeded: Conn) -> None:
    served = await compare(seeded, ["acme", "beta"], metrics=["revenue", "net_income"])

    data = served.data
    assert data.period_type == "TTM"
    assert [company.ticker for company in data.companies] == ["ACME", "BETA"]
    revenue, net_income = data.metrics
    assert {ticker: row.value if row else None for ticker, row in revenue.values.items()} == {
        "ACME": Decimal(3800),
        "BETA": Decimal(5000),
    }
    assert net_income.values["BETA"] is None  # no filing of BETA reports it
    assert served.quality_flags == (
        FlagRef(code="outlier_jump", metric="revenue", period_end=date(2026, 6, 30), ticker="BETA"),
    )
    with pytest.raises(InvalidParameter, match="2 to 5 tickers"):
        await compare(seeded, ["ACME"])
    with pytest.raises(InvalidParameter, match="same company"):
        await compare(seeded, ["ACME", "ACME-PA"])


async def test_filings_include_the_predecessors(seeded: Conn) -> None:
    served = await recent_filings(seeded, "ACME", limit=10)

    listed = [(f.accession_no, f.cik) for f in served.data.filings]
    assert listed[0] == (Q3_RELEASE, ACME)
    assert (OLD_Q1_10Q, OLD) in listed and (PROSPECTUS, ACME) in listed
    q3 = served.data.filings[0]
    assert [(e.item_or_form, e.label) for e in q3.events] == [
        ("2.02", "Quarterly or annual results")
    ]


async def test_unknown_tickers(seeded: Conn) -> None:
    with pytest.raises(NotFound, match="No security has the ticker NOPE"):
        await latest_earnings(seeded, "NOPE")
    with pytest.raises(InvalidParameter):
        await get_financials(seeded, "no such ticker!")
    with pytest.raises(NotFound, match="BETA"):
        await latest_earnings(seeded, "BETA")
