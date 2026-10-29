"""E5a: a small hand-made dataset for the data API's service and route tests, written with SQL
inside the test's transaction.

ACME (CIK 1001) took over from Acme Holdings (1000), whose filings hold ACME's first quarter of
2026 (a predecessor, D-068). ACME's second-quarter release states no period end; its values
were cross-checked with the 10-Q. Its third-quarter release failed verification. A fiscal 2020
10-K is older than the filings backfill, so only its facts name it. BETA is a second company to
compare with; FNDX is a fund and FPIX a foreign private issuer; OUTC is a domestic filer outside
the universe.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

Conn = psycopg.AsyncConnection[Any]

OLD, ACME, BETA, FUND, FPI, OUTSIDE = 1000, 1001, 2002, 3003, 4004, 5005
# Filings
OLD_Q1_RELEASE = "0001000000-26-000008"
OLD_Q1_10Q = "0001000000-26-000010"
Q2_RELEASE = "0001001000-26-000020"
Q2_10Q = "0001001000-26-000025"
PROSPECTUS = "0001001000-26-000030"
Q3_RELEASE = "0001001000-26-000040"
FY2020_10K = "0001001000-21-000005"  # older than the filings backfill: known from facts only
BETA_10Q = "0002002000-26-000011"
CALC = "2.3"


async def seed(conn: Conn) -> None:
    for cik, name, filer_type, in_universe in [
        (OLD, "Acme Holdings Inc", "domestic", False),
        (ACME, "Acme Corp", "domestic", True),
        (BETA, "Beta Inc", "domestic", True),
        (FUND, "Index Fund Trust", "fund", True),
        (FPI, "Foreign Issuer plc", "foreign_private_issuer", True),
        (OUTSIDE, "Outside Corp", "domestic", False),
    ]:
        await conn.execute(
            "insert into companies (cik, name, sic_code, sic_description, fiscal_year_end, "
            "state_of_inc, filer_type, in_universe) values "
            "(%s, %s, '3571', 'Electronic computers', '1231', 'DE', %s, %s)",
            [cik, name, filer_type, in_universe],
        )
    for cik, ticker, security_type in [
        (ACME, "ACME", "common"),
        (ACME, "ACME-PA", "preferred"),
        (BETA, "BETA", "common"),
        (FUND, "FNDX", "fund"),
        (FPI, "FPIX", "common"),
        (OUTSIDE, "OUTC", "common"),
    ]:
        await conn.execute(
            "insert into securities (cik, ticker, security_type, exchange, valid_from) "
            "values (%s, %s, %s, 'NYSE', '2026-10-25')",
            [cik, ticker, security_type],
        )
    await conn.execute(
        "insert into company_predecessors (cik, predecessor_cik, source_url, note) values "
        "(%s, %s, 'https://example.com/8-k12b', 'test succession')",
        [ACME, OLD],
    )
    await _filings(conn)
    await _releases(conn)
    await _metrics(conn)
    await conn.execute(
        "insert into xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "fy, fp, form, filed_at, accession_no) values "
        "(%s, 'us-gaap', 'Revenues', 'USD', '2020-01-01', '2020-12-31', 3000, 2020, 'FY', "
        "'10-K', '2021-02-20', %s)",
        [ACME, FY2020_10K],
    )


async def _filings(conn: Conn) -> None:
    rows: list[tuple[str, int, str, str, str | None, str | None, str | None]] = [
        (OLD_Q1_RELEASE, OLD, "8-K", "2026-04-28", "2026-04-28T20:05:00Z", None, "2.02,9.01"),
        (OLD_Q1_10Q, OLD, "10-Q", "2026-05-01", "2026-05-01T12:00:00Z", "2026-03-31", None),
        (Q2_RELEASE, ACME, "8-K", "2026-07-28", "2026-07-28T20:05:00Z", None, "2.02,9.01"),
        (Q2_10Q, ACME, "10-Q", "2026-08-05", "2026-08-05T12:00:00Z", "2026-06-30", None),
        (PROSPECTUS, ACME, "424B2", "2026-08-10", "2026-08-10T15:00:00Z", None, None),
        (Q3_RELEASE, ACME, "8-K", "2026-10-28", "2026-10-28T20:05:00Z", None, "2.02,9.01"),
        (BETA_10Q, BETA, "10-Q", "2026-08-01", "2026-08-01T12:00:00Z", "2026-06-30", None),
    ]
    for accession, cik, form, filed, accepted, period, items in rows:
        await conn.execute(
            "insert into filings (accession_no, cik, form, filed_at, accepted_at, "
            "period_of_report, items_8k) values (%s, %s, %s, %s, %s, %s, "
            "string_to_array(%s, ','))",
            [accession, cik, form, filed, accepted, period, items],
        )


def _report(slots: dict[str, dict[str, Any]], notes: tuple[str, ...] = ()) -> str:
    return json.dumps({"form": "8-K", "notes": list(notes), "slots": slots, "exhibits": []})


_PASSED = {"checks": ["in_source", "scale", "sanity"], "problems": [], "note": None}


async def _releases(conn: Conn) -> None:
    releases: list[tuple[Any, ...]] = [
        # accession, cik, accepted, fiscal year, period, end, label, status, report
        (
            OLD_Q1_RELEASE,
            OLD,
            datetime(2026, 4, 28, 20, 5, tzinfo=UTC),
            2026,
            "Q1",
            date(2026, 3, 31),
            "Three Months Ended March 31, 2026",
            "complete",
            _report({"revenue": _PASSED}),
        ),
        (
            Q2_RELEASE,
            ACME,
            datetime(2026, 7, 28, 20, 5, tzinfo=UTC),
            None,
            None,
            None,  # the release states no period end
            "Second Quarter 2026",
            "complete",
            _report({"revenue": _PASSED, "eps_diluted": _PASSED}),
        ),
        (
            Q3_RELEASE,
            ACME,
            datetime(2026, 10, 28, 20, 5, tzinfo=UTC),
            2026,
            "Q3",
            date(2026, 9, 30),
            "Three Months Ended September 30, 2026",
            "failed",
            _report(
                {"revenue": {"checks": [], "problems": ["not in the source"], "note": None}},
                ("the exhibit is printed as pictures",),
            ),
        ),
    ]
    for accession, cik, accepted, year, period, end, label, status, report in releases:
        await conn.execute(
            "insert into earnings_releases (accession_no, cik, accepted_at, doc_name, "
            "fiscal_year, fiscal_period, period_end, period_label, status, extractor, "
            "extractor_version, check_report, verified_at) values (%s, %s, %s, 'ex99.htm', "
            "%s, %s, %s, %s, %s, 'rules', '3', %s, %s)",
            [
                *(accession, cik, accepted, year, period, end, label, status, report),
                accepted if status in ("complete", "partial") else None,
            ],
        )
    facts: list[tuple[Any, ...]] = [
        # accession, slot, value, unit, prior, change, status, cross-check status, value, filing
        (OLD_Q1_RELEASE, "revenue", 900, "USD", 800, "12.5", "passed", "pending", None, None),
        (Q2_RELEASE, "revenue", 1000, "USD", 950, "5.3", "passed", "matched", 1000, Q2_10Q),
        (
            Q2_RELEASE,
            "eps_diluted",
            "1.25",
            "USD/shares",
            "1.10",
            "13.6",
            "passed",
            "mismatch",
            "1.20",
            Q2_10Q,
        ),
        (Q3_RELEASE, "revenue", None, "USD", None, None, "failed", "pending", None, None),
    ]
    for accession, slot, value, unit, prior, change, status, cross, filed, filing in facts:
        flags = ["release_vs_filing_mismatch"] if cross == "mismatch" else []
        await conn.execute(
            "insert into earnings_facts (accession_no, slot, value, unit, prior_value, "
            "change_pct, source_row_label, source_quote, scale_text, method, extractor_version, "
            "check_status, cross_check_status, cross_check_value, cross_check_accession, "
            "quality_flags) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'rule', '3', %s, %s, "
            "%s, %s, %s)",
            [
                accession,
                slot,
                value,
                unit,
                prior,
                change,
                "Total revenues" if status == "passed" else None,
                f"Total revenues | {prior} | {value}" if status == "passed" else None,
                "in millions, except per share amounts" if status == "passed" else None,
                status,
                cross,
                filed,
                filing,
                flags,
            ],
        )


async def metric(
    conn: Conn,
    cik: int,
    name: str,
    period_type: str,
    end: str,
    value: int | str | None,
    *,
    accession: str | None,
    fiscal: tuple[int, str],
    filed: str | None = None,
    method: str = "reported",
    concept: str | None = "Revenues",
    unit: str = "USD",
    start: str | None = None,
    flags: tuple[str, ...] = (),
    sources: tuple[str, ...] | None = None,
) -> None:
    await conn.execute(
        "insert into financial_metrics (cik, metric, period_type, fiscal_year, fiscal_period, "
        "period_start, period_end, value, unit, method, concept, accession_no, "
        "source_accessions, filed_at, quality_flags, calc_version) values "
        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            cik,
            name,
            period_type,
            fiscal[0],
            fiscal[1],
            start,
            end,
            Decimal(value) if value is not None else None,
            unit,
            method,
            concept,
            accession,
            list(sources if sources is not None else ((accession,) if accession else ())),
            filed,
            list(flags),
            CALC,
        ],
    )


async def _metrics(conn: Conn) -> None:
    q2, q1 = (2026, "Q2"), (2026, "Q1")
    # Revenue by quarter: Q1 under the predecessor, Q2 the company's own.
    await metric(
        conn,
        OLD,
        "revenue",
        "Q",
        "2026-03-31",
        900,
        accession=OLD_Q1_10Q,
        fiscal=q1,
        filed="2026-05-01",
        start="2026-01-01",
    )
    await metric(
        conn,
        ACME,
        "revenue",
        "Q",
        "2026-06-30",
        1000,
        accession=Q2_10Q,
        fiscal=q2,
        filed="2026-08-05",
        start="2026-04-01",
    )
    # A year from a filing older than the filings backfill.
    await metric(
        conn,
        ACME,
        "revenue",
        "FY",
        "2020-12-31",
        3000,
        accession=FY2020_10K,
        fiscal=(2020, "FY"),
        filed="2021-02-20",
        start="2020-01-01",
    )
    # Trailing twelve months: revenue (summed over both companies' quarters), net income, a gap.
    await metric(
        conn,
        ACME,
        "revenue",
        "TTM",
        "2026-06-30",
        3800,
        accession=Q2_10Q,
        fiscal=q2,
        filed="2026-08-05",
        method="derived_ttm",
        start="2025-07-01",
        sources=(OLD_Q1_10Q, Q2_10Q),
    )
    await metric(
        conn,
        ACME,
        "net_income",
        "TTM",
        "2026-06-30",
        400,
        accession=Q2_10Q,
        fiscal=q2,
        filed="2026-08-05",
        method="derived_ttm",
        concept="NetIncomeLoss",
        start="2025-07-01",
    )
    await metric(
        conn,
        ACME,
        "operating_cash_flow",
        "TTM",
        "2026-06-30",
        None,
        accession=None,
        fiscal=q2,
        method="derived_ttm",
        concept=None,
        flags=("ttm_gap",),
    )
    await metric(
        conn,
        ACME,
        "net_margin",
        "TTM",
        "2026-06-30",
        "10.5",
        accession=Q2_10Q,
        fiscal=q2,
        filed="2026-08-05",
        method="computed",
        concept=None,
        unit="percent",
        start="2025-07-01",
    )
    # BETA, to compare with: revenue with a flag.
    await metric(
        conn,
        BETA,
        "revenue",
        "TTM",
        "2026-06-30",
        5000,
        accession=BETA_10Q,
        fiscal=q2,
        filed="2026-08-01",
        method="derived_ttm",
        start="2025-07-01",
        flags=("outlier_jump",),
    )
