"""E9-S4: storing a report builds the manager's quarter again from all its reports: lines merged,
weights, amendments in filing order; a stored position that changes is logged and announced as
a value change (§9.8, D-027)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from conftest import ROOT, connect_to, filing_routes, sec_serving

from marketdata.db.repositories import filings, holdings, managers, value_changes
from marketdata.db.repositories.managers import CuratedManager
from marketdata.ingest.f13 import F13Ingest, F13Report, read_unit_overrides
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.filings import FilingRow
from marketdata.normalize.holdings import UnitOverride
from marketdata.normalize.webhooks import WEBHOOK_PUBLISH
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks import queue

Conn = psycopg.AsyncConnection[Any]
BERKSHIRE, HIMALAYA, VALUEACT, BAUPOST = 1067983, 1709323, 1418814, 1061768
LISTINGS = {
    "0001193125-26-352200": (BERKSHIRE, "13F-HR", date(2026, 8, 14), date(2026, 6, 30)),
    "0002043585-26-000011": (HIMALAYA, "13F-HR", date(2026, 2, 17), date(2025, 12, 31)),
    "0002043585-26-000012": (HIMALAYA, "13F-HR/A", date(2026, 2, 19), date(2025, 12, 31)),
    "0001418812-24-000021": (VALUEACT, "13F-HR", date(2024, 11, 14), date(2024, 9, 30)),
    "0001418812-24-000022": (VALUEACT, "13F-HR/A", date(2024, 11, 15), date(2024, 9, 30)),
    "0001061768-26-000010": (BAUPOST, "13F-HR", date(2026, 8, 13), date(2026, 6, 30)),
}
Q4_2025 = date(2025, 12, 31)


async def _seed(conn: Conn) -> None:
    curated = [
        CuratedManager(cik, f"filer {cik}", f"Manager {cik}", "test")
        for cik in (BERKSHIRE, HIMALAYA, VALUEACT, BAUPOST)
    ]
    await managers.replace_curated(conn, [manager.manager_cik for manager in curated], curated)
    await filings.insert_new(
        conn,
        [
            FilingRow(accession, cik, form, filed, None, period, None, None, None, "028-00001")
            for accession, (cik, form, filed, period) in LISTINGS.items()
        ],
    )


async def _store(
    conn: Conn, tmp_path: Path, accession: str, overrides: tuple[UnitOverride, ...] = ()
) -> F13Report:
    cik = LISTINGS[accession][0]
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await F13Ingest(fetcher, connect_to(conn), overrides).run(cik, accession)


async def test_lines_merge_into_positions_weighted_among_the_long_equity_ones(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)

    report = await _store(conn, tmp_path, "0001193125-26-352200")

    assert (report.lines, report.positions, report.changes) == (89, 29, 0)
    stored = await holdings.positions(conn, BERKSHIRE, date(2026, 6, 30))
    apple = stored[0]  # the largest
    assert (apple.cusip, apple.lines, apple.shares) == ("037833100", 12, Decimal("227917808"))
    ally = next(position for position in stored if position.cusip == "02005N100")
    assert (ally.lines, ally.shares, ally.source_accessions) == (
        6,
        Decimal("27000000"),
        ["0001193125-26-352200"],
    )
    weights = [position.weight_pct for position in stored]
    assert all(weight is not None for weight in weights)
    assert abs(sum(weight for weight in weights if weight) - 100) < Decimal("0.01")


async def test_a_restatement_changes_the_stored_positions_and_says_so(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)
    await _store(conn, tmp_path, "0002043585-26-000011")

    report = await _store(conn, tmp_path, "0002043585-26-000012")

    # Himalaya had Alphabet's classes the wrong way round: two positions, shares and value each.
    assert report.changes == 4
    stored = {p.cusip: p for p in await holdings.positions(conn, HIMALAYA, Q4_2025)}
    assert stored["02079K305"].shares == Decimal("2543300")
    assert stored["02079K305"].source_accessions == ["0002043585-26-000012"]
    changes = await value_changes.page(conn, [HIMALAYA])
    assert {(c.entity, c.reason) for c in changes} == {("fund_position", "amendment")}
    shares = next(
        c
        for c in changes
        if c.entity_key
        == {
            "manager_cik": "1709323",
            "report_period": "2025-12-31",
            "cusip": "02079K305",
            "field": "shares",
        }
    )
    assert (shares.old_value, shares.new_value) == (Decimal("2451300"), Decimal("2543300"))
    assert shares.detail == (
        "13F-HR/A 0002043585-26-000012 (RESTATEMENT), filed 2026-02-19, amends the report for "
        "2025-12-31"
    )
    assert len(await queue.list_tasks(conn, kind=WEBHOOK_PUBLISH)) == 4  # data.changed


async def test_the_same_quarter_whichever_report_is_stored_first(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)
    await _store(conn, tmp_path, "0002043585-26-000012")

    report = await _store(conn, tmp_path, "0002043585-26-000011")

    assert report.changes == 0  # the restatement still replaces the report
    stored = {p.cusip: p.shares for p in await holdings.positions(conn, HIMALAYA, Q4_2025)}
    assert stored["02079K305"] == Decimal("2543300")


async def test_new_holdings_add_a_position_and_its_repeats_count_once(
    conn: Conn, tmp_path: Path
) -> None:
    await _seed(conn)
    await _store(conn, tmp_path, "0001418812-24-000021")

    report = await _store(conn, tmp_path, "0001418812-24-000022")

    assert (report.positions, report.changes) == (11, 0)
    stored = {p.cusip: p for p in await holdings.positions(conn, VALUEACT, date(2024, 9, 30))}
    assert stored["254687106"].shares == Decimal("7464831")  # Disney, once
    assert stored["888787108"].source_accessions == ["0001418812-24-000022"]  # Toast


async def test_an_override_corrects_the_stored_values(conn: Conn, tmp_path: Path) -> None:
    await _seed(conn)
    await _store(conn, tmp_path, "0001061768-26-000010")  # read in dollars, as the date says
    overrides = read_unit_overrides(ROOT / "data" / "f13_overrides.toml")

    report = await _store(conn, tmp_path, "0001061768-26-000010", overrides)

    assert report.changes == report.positions == 23  # each value, none of the shares
    changes = await value_changes.page(conn, [BAUPOST], limit=50)
    assert {(c.reason, c.entity_key["field"]) for c in changes} == {("correction", "value_usd")}
    alphabet = next(c for c in changes if c.entity_key["cusip"] == "02079K107")
    assert (alphabet.old_value, alphabet.new_value) == (Decimal("484744"), Decimal("484744000"))
    assert alphabet.detail is not None
    assert alphabet.detail.startswith(
        "13F-HR 0001061768-26-000010 for 2026-06-30: values read in thousands, not dollars"
    )
