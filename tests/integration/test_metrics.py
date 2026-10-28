"""E4a-S4: each facts refresh rebuilds the company's reported metrics. A value a later filing
reports differently writes a `restatement` change, a new calc version that changes a value a
`correction` (D-027); a company's series merges its predecessors, its own rows winning (D-068).
"""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import SEC_FIXTURES, connect_to, sec_serving
from typer.testing import CliRunner

from marketdata import cli
from marketdata.db.repositories import financial_metrics, predecessors, value_changes
from marketdata.db.repositories.predecessors import CompanyPredecessor
from marketdata.ingest.facts import FactsLoader, FactsReport
from marketdata.ingest.fetch import RawFetcher
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.errors import InvalidParameter
from marketdata.service.financials import metric_series

Conn = psycopg.AsyncConnection[Any]

AAPL, JPM, XOM, EXXON = 320193, 19617, 2115436, 34088
AAPL_FACTS = (SEC_FIXTURES / "companyfacts" / "AAPL.json").read_bytes()
Q3 = ("2026-03-29", "2026-06-27")  # Apple's fiscal 2026 Q3


async def _company(
    conn: Conn, cik: int, *, sic: str = "3571", fye: str = "0926", in_universe: bool = True
) -> None:
    await conn.execute(
        "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, in_universe) "
        "values (%s, 'test', %s, %s, 'domestic', %s)",
        [cik, sic, fye, in_universe],
    )


async def _refresh(
    conn: Conn, tmp_path: Path, cik: int, routes: dict[str, bytes | int]
) -> FactsReport:
    async with sec_serving(routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await FactsLoader(fetcher, connect_to(conn)).refresh(cik)


def _restated(data: bytes) -> bytes:
    """Apple's file with a later 10-Q/A that reports Q3 revenue again, 1 million lower."""
    document = json.loads(data)
    rows = document["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]
    usd = rows["units"]["USD"]
    [original] = [row for row in usd if (row.get("start"), row["end"]) == Q3 and "frame" in row]
    usd.append(
        {
            **{key: value for key, value in original.items() if key != "frame"},
            "val": original["val"] - 1_000_000,
            "accn": "0000320193-26-000099",
            "form": "10-Q/A",
            "filed": "2026-09-15",
        }
    )
    return json.dumps(document).encode()


def _facts(cik: int, accession: str, filed: str, form: str, assets: dict[str, int]) -> bytes:
    rows = [
        {
            "end": end,
            "val": value,
            "accn": accession,
            "fy": 2026,
            "fp": "Q2",
            "form": form,
            "filed": filed,
        }
        for end, value in assets.items()
    ]
    income = [
        {
            "start": "2024-01-01",
            "end": "2024-12-31",
            "val": 25,
            "accn": accession,
            "fy": 2025,
            "fp": "FY",
            "form": form,
            "filed": filed,
        },
        {
            "start": "2025-01-01",
            "end": "2025-12-31",
            "val": 30,
            "accn": accession,
            "fy": 2025,
            "fp": "FY",
            "form": form,
            "filed": filed,
        },
        {
            "start": "2026-04-01",
            "end": "2026-06-30",
            "val": 8,
            "accn": accession,
            "fy": 2026,
            "fp": "Q2",
            "form": form,
            "filed": filed,
        },
    ]
    facts = {
        "us-gaap": {"Assets": {"units": {"USD": rows}}, "NetIncomeLoss": {"units": {"USD": income}}}
    }
    return json.dumps({"cik": cik, "facts": facts}).encode()


async def test_a_refresh_builds_the_companys_metrics_and_a_second_changes_nothing(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)
    routes: dict[str, bytes | int] = {urls.companyfacts(AAPL): AAPL_FACTS}

    first = await _refresh(conn, tmp_path, AAPL, routes)
    again = await _refresh(conn, tmp_path, AAPL, routes)

    built = first.files[0].metrics
    assert built is not None and built.rows == built.written == 1848
    assert again.files[0].metrics is not None and again.files[0].metrics.written == 0
    assert await financial_metrics.count(conn, AAPL) == 1848
    [q3] = [
        row
        for row in await financial_metrics.series(conn, [AAPL], "revenue", "Q")
        if row.period_end == date(2026, 6, 27)
    ]
    assert (q3.fiscal_year, q3.fiscal_period, q3.value) == (2026, "Q3", Decimal(109417000000))
    assert await value_changes.for_metric(conn, AAPL, "revenue") == []


async def test_a_later_filing_that_changes_a_value_is_a_restatement(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)
    await _refresh(conn, tmp_path, AAPL, {urls.companyfacts(AAPL): AAPL_FACTS})

    report = await _refresh(conn, tmp_path, AAPL, {urls.companyfacts(AAPL): _restated(AAPL_FACTS)})

    assert report.files[0].metrics is not None and report.files[0].metrics.changes == 1
    [change] = await value_changes.for_metric(conn, AAPL, "revenue")
    assert (change.entity, change.reason) == ("financial_metric", "restatement")
    assert change.entity_key == {"metric": "revenue", "period_type": "Q", "period_end": Q3[1]}
    assert (change.old_value, change.new_value) == (Decimal(109417000000), Decimal(109416000000))
    assert change.detail == (
        "0000320193-26-000099, filed 2026-09-15, restates 0000320193-26-000020 of 2026-07-31"
    )


async def test_a_new_calc_version_that_changes_a_value_is_a_correction(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AAPL)
    await _refresh(conn, tmp_path, AAPL, {urls.companyfacts(AAPL): AAPL_FACTS})
    # As if an earlier version of the rules had stored another value for the quarter.
    await conn.execute(
        "update financial_metrics set value = 1, calc_version = '0.9' "
        "where cik = %s and metric = 'revenue' and period_type = 'Q' and period_end = %s",
        [AAPL, date(2026, 6, 27)],
    )

    async with sec_serving({}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        built = await FactsLoader(fetcher, connect_to(conn)).rebuild_metrics(AAPL)

    assert (built.written, built.changes) == (1, 1)
    [change] = await value_changes.for_metric(conn, AAPL, "revenue")
    assert (change.reason, change.detail) == ("correction", "calc version 0.9 to 1.1")
    assert (change.old_value, change.new_value) == (Decimal(1), Decimal(109417000000))


async def test_a_banks_revenue_is_stored_as_not_applicable(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, JPM, sic="6021", fye="1231")
    data = _facts(JPM, "0000019617-26-000050", "2026-08-01", "10-Q", {"2026-06-30": 4_500})

    await _refresh(conn, tmp_path, JPM, {urls.companyfacts(JPM): data})

    [row] = await financial_metrics.series(conn, [JPM], "revenue", "Q")
    assert (row.value, row.quality_flags, row.concept) == (
        None,
        ["not_applicable_financials"],
        None,
    )


async def test_a_series_merges_the_predecessors_and_the_company_wins(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, XOM, sic="2911", fye="1231")
    await _company(conn, EXXON, sic="2911", fye="1231", in_universe=False)
    link = CompanyPredecessor(
        cik=XOM,
        predecessor_cik=EXXON,
        source_url=urls.filing_folder(XOM, "0001193125-26-291990"),
        note="test",
    )
    await predecessors.replace_all(conn, [link])
    routes: dict[str, bytes | int] = {
        # The joint 10-Q's facts are under the new CIK, back to the year end (EDGE_CASES.md).
        urls.companyfacts(XOM): _facts(
            XOM,
            "0000034088-26-000093",
            "2026-08-03",
            "10-Q",
            {"2026-06-30": 460, "2025-12-31": 449},
        ),
        urls.companyfacts(EXXON): _facts(
            EXXON,
            "0000034088-26-000045",
            "2026-02-18",
            "10-K",
            {"2025-12-31": 448, "2024-12-31": 453},
        ),
    }
    await _refresh(conn, tmp_path, XOM, routes)

    series = await metric_series(conn, XOM, "total_assets", "Q")

    assert [(row.period_end, row.value, row.cik) for row in series] == [
        (date(2026, 6, 30), Decimal(460), XOM),
        (date(2025, 12, 31), Decimal(449), XOM),  # both report it: the company's own row
        (date(2024, 12, 31), Decimal(453), EXXON),
    ]
    assert series[0].proof.filing_url == urls.filing_index_page(XOM, "0000034088-26-000093")
    assert series[0].proof.concept == "Assets" and series[0].proof.method == "reported"


async def test_an_unknown_metric_or_period_is_refused(conn: Conn) -> None:
    with pytest.raises(InvalidParameter, match="unknown metric"):
        await metric_series(conn, AAPL, "ebitda", "Q")
    with pytest.raises(InvalidParameter, match="TTM comes with E4b"):
        await metric_series(conn, AAPL, "revenue", "TTM")


@pytest.fixture
def stored_apple(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Apple's company, ticker and two revenue rows, committed: the CLI opens its own
    connection. Removed afterwards."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) "
            "values (%s, 'Apple Inc.', 'domestic', true)",
            [AAPL],
        )
        conn.execute(
            "insert into securities (cik, ticker, valid_from) values (%s, 'AAPL', '2026-10-25')",
            [AAPL],
        )
        for start, end, value, accession, filed, fiscal in (
            ("2026-03-29", "2026-06-27", 109417000000, "0000320193-26-000020", "2026-07-31", "Q3"),
            ("2025-12-28", "2026-03-28", 111184000000, "0000320193-26-000013", "2026-05-01", "Q2"),
        ):
            conn.execute(
                "insert into financial_metrics (cik, metric, period_type, fiscal_year, "
                "fiscal_period, period_start, period_end, value, unit, method, concept, "
                "accession_no, source_accessions, filed_at, calc_version) values (%s, 'revenue', "
                "'Q', 2026, %s, %s, %s, %s, 'USD', 'reported', "
                "'RevenueFromContractWithCustomerExcludingAssessedTax', %s, %s, %s, '1.1')",
                [AAPL, fiscal, start, end, value, accession, [accession], filed],
            )
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from financial_metrics where cik = %s", [AAPL])
        conn.execute("delete from securities where cik = %s", [AAPL])
        conn.execute("delete from companies where cik = %s", [AAPL])


def test_metrics_show_prints_values_with_their_filings(stored_apple: str) -> None:
    result = CliRunner().invoke(cli.app, ["metrics", "show", "aapl", "--metric", "revenue"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Apple Inc. (AAPL, CIK 320193): revenue, quarters"
    assert "2026 Q3" in lines[1] and "109,417,000,000 USD" in lines[1]
    assert "0000320193-26-000020 filed 2026-07-31" in lines[1]
    assert lines[2].strip() == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert "2026 Q2" in lines[3]
    assert lines[-1] == "Information from public filings. Not investment advice."


def test_metrics_show_refuses_an_unknown_metric(stored_apple: str) -> None:
    result = CliRunner().invoke(cli.app, ["metrics", "show", "AAPL", "--metric", "ebitda"])

    assert result.exit_code == 2
    assert "unknown metric 'ebitda'" in result.output
