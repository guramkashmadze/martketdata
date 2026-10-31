"""E9-S6: portfolios as the API and MCP serve them: managers with their latest report, a
manager's positions with weights and change types from the quarter before (split-adjusted, the
unit check across managers, positions exited listed last), and a company's holders by manager
name (§9.8, D-029)."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fund_world import APPLE, NETFLIX, Q1, Q2, TOAST, Line, curate, link, report

from marketdata.service import funds
from marketdata.service.errors import InvalidParameter, NotFound

Conn = psycopg.AsyncConnection[Any]
BERKSHIRE, BRIDGEWATER, RENAISSANCE = 1067983, 1350694, 1037389
MICROSOFT, COKE = "594918104", "191216100"
A_Q1, A_Q2 = "0000000001-26-000001", "0000000001-26-000002"
B_Q1, B_Q2, C_Q2 = "0000000002-26-000001", "0000000002-26-000002", "0000000003-26-000002"


@pytest.fixture
async def world(conn: Conn, tmp_path: Path) -> None:
    await curate(
        conn,
        {
            BERKSHIRE: "Berkshire Hathaway",
            BRIDGEWATER: "Bridgewater Associates",
            RENAISSANCE: "Renaissance Technologies",
        },
    )
    await report(
        conn,
        tmp_path,
        BERKSHIRE,
        A_Q1,
        Q1.replace(month=5, day=15),
        Q1,
        [
            Line(APPLE, 100_000, 20_000_000, "APPLE INC"),
            Line(NETFLIX, 1_000, 1_000_000, "NETFLIX INC"),
            Line(TOAST, 50_000, 2_000_000, "TOAST INC"),
            Line(MICROSOFT, 10_000, 4_000_000, "MICROSOFT CORP"),
        ],
    )
    await report(
        conn,
        tmp_path,
        BERKSHIRE,
        A_Q2,
        Q2.replace(month=8, day=14),
        Q2,
        [
            Line(APPLE, 100_500, 21_105_000, "APPLE INC"),  # +0.5%: unchanged
            Line(NETFLIX, 10_000, 980_000, "NETFLIX INC"),  # 10 for 1: unchanged
            Line(MICROSOFT, 12_000, 5_040_000, "MICROSOFT CORP"),  # +20%: added
            Line(COKE, 5_000, 350_000, "COCA COLA CO"),  # new
            Line(APPLE, 1_000, 210_000, "APPLE INC", put_call="Call"),  # new, no weight
        ],  # Toast: exited
    )
    await report(
        conn,
        tmp_path,
        BRIDGEWATER,
        B_Q1,
        Q1.replace(month=5, day=14),
        Q1,
        [Line(NETFLIX, 2_000, 2_000_000, "NETFLIX INC")],
    )
    await report(
        conn,
        tmp_path,
        BRIDGEWATER,
        B_Q2,
        Q2.replace(month=8, day=13),
        Q2,
        [
            Line(NETFLIX, 20_000, 1_960_000, "NETFLIX INC"),  # exactly 10 times: the split
            Line(APPLE, 5_000, 1_050_000, "APPLE INC"),
        ],
    )
    # Renaissance reports Apple in thousands of dollars: $0.21 a share against $210.
    await report(
        conn,
        tmp_path,
        RENAISSANCE,
        C_Q2,
        Q2.replace(month=8, day=12),
        Q2,
        [Line(APPLE, 2_000, 420)],
    )
    await link(conn, APPLE, "AAPL", 320193)


async def test_the_curated_managers_with_their_latest_report(conn: Conn, world: None) -> None:
    served = await funds.list_funds(conn)

    rows = [(f.manager, f.quarter, f.filed_at.isoformat(), f.positions) for f in served.data.funds]
    assert rows == [
        ("Berkshire Hathaway", "2026Q2", "2026-08-14", 5),
        ("Bridgewater Associates", "2026Q2", "2026-08-13", 2),
        ("Renaissance Technologies", "2026Q2", "2026-08-12", 1),
    ]
    berkshire = served.data.funds[0]
    assert berkshire.long_value_usd == Decimal("27475000")  # the call left out
    assert [s.accession_no for s in served.sources] == [A_Q2, B_Q2, C_Q2]


async def test_positions_largest_first_with_their_changes_then_those_exited(
    conn: Conn, world: None
) -> None:
    served = await funds.fund_holdings(conn, BERKSHIRE)

    data = served.data
    assert (data.quarter, data.report_period, data.previous_period) == ("2026Q2", Q2, Q1)
    assert (data.positions_count, data.long_value_usd) == (5, Decimal("27475000"))
    assert [(p.cusip, p.put_call, p.change_type) for p in data.positions] == [
        (APPLE, None, "unchanged"),
        (MICROSOFT, None, "added"),
        (NETFLIX, None, "unchanged"),
        (COKE, None, "new"),
        (APPLE, "CALL", "new"),
        (TOAST, None, "exited"),
    ]
    apple, microsoft, netflix, _, call, toast = data.positions
    assert (apple.ticker, apple.company_cik, apple.weight_pct) == (
        "AAPL",
        320193,
        Decimal("76.8153"),
    )
    assert (microsoft.previous_shares, microsoft.shares_change) == (Decimal(10000), Decimal(2000))
    assert (netflix.split_ratio, netflix.shares_change, netflix.quality_flags) == (
        "10:1",
        Decimal(0),
        ("split_adjusted",),
    )
    assert call.weight_pct is None
    assert (toast.shares, toast.previous_shares, toast.shares_change) == (
        Decimal(0),
        Decimal(50000),
        Decimal(-50000),
    )
    assert toast.proof.accession_nos == (A_Q1,)
    assert (apple.proof.accession_nos, apple.proof.lines, apple.proof.method) == (
        (A_Q2,),
        1,
        "f13_xml",
    )
    assert {c.change_type: c.positions for c in data.changes} == {
        "new": 2,
        "added": 1,
        "trimmed": 0,
        "unchanged": 2,
        "exited": 1,
    }
    [filing] = data.filings
    assert filing.filing_url.endswith("/data/1067983/000000000126000002/")
    assert filing.table_url is not None and filing.table_url.endswith("/56757.xml")
    assert [source.accession_no for source in served.sources] == [A_Q2]


async def test_a_change_filter_and_pages(conn: Conn, world: None) -> None:
    first = await funds.fund_holdings(conn, BERKSHIRE, change=["new", "exited"], limit=2)
    assert first.data.next_cursor is not None
    second = await funds.fund_holdings(
        conn, BERKSHIRE, change=["new", "exited"], limit=2, cursor=first.data.next_cursor
    )

    assert [(p.cusip, p.put_call) for p in first.data.positions] == [(COKE, None), (APPLE, "CALL")]
    assert [p.cusip for p in second.data.positions] == [TOAST]
    assert second.data.next_cursor is None
    with pytest.raises(InvalidParameter, match="change types are"):
        await funds.fund_holdings(conn, BERKSHIRE, change=["bought"])


async def test_a_quarter_without_the_one_before_has_no_change_types(
    conn: Conn, world: None
) -> None:
    served = await funds.fund_holdings(conn, "berkshire", quarter="2026Q1")

    assert served.data.previous_period is None
    assert all(p.change_type is None for p in served.data.positions)
    assert all("no_prior_report" in p.quality_flags for p in served.data.positions)
    with pytest.raises(NotFound, match="no 13F report for 2025Q4; it has 2026Q2, 2026Q1"):
        await funds.fund_holdings(conn, BERKSHIRE, quarter="2025-12-31")
    with pytest.raises(InvalidParameter, match="2026Q2 or 2026-06-30"):
        await funds.fund_holdings(conn, BERKSHIRE, quarter="last quarter")


async def test_a_value_far_from_the_other_managers_price_is_unit_suspect(
    conn: Conn, world: None
) -> None:
    served = await funds.fund_holdings(conn, RENAISSANCE)

    [apple] = served.data.positions
    assert apple.quality_flags == ("unit_suspect", "no_prior_report")
    assert [(f.code, f.metric, f.ticker) for f in served.quality_flags] == [
        ("unit_suspect", "fund_position", "AAPL"),
        ("no_prior_report", "fund_position", "AAPL"),
    ]
    berkshire = await funds.fund_holdings(conn, BERKSHIRE)
    assert "unit_suspect" not in berkshire.data.positions[0].quality_flags


async def test_a_companys_holders_by_manager_name(conn: Conn, world: None) -> None:
    served = await funds.company_funds(conn, "aapl")

    data = served.data
    assert (data.ticker, data.cusips) == ("AAPL", (APPLE,))
    assert [(h.manager, h.position.put_call, h.position.change_type) for h in data.holders] == [
        ("Berkshire Hathaway", None, "unchanged"),
        ("Berkshire Hathaway", "CALL", "new"),
        ("Bridgewater Associates", None, "new"),
        ("Renaissance Technologies", None, None),
    ]
    assert data.holders[0].filing_url.endswith("/data/1067983/000000000126000002/")
    assert {source.accession_no for source in served.sources} == {A_Q2, B_Q2, C_Q2}


async def test_a_company_no_curated_manager_holds_has_no_holders(conn: Conn, world: None) -> None:
    await link(conn, "88160R101", "TSLA", 1318605)

    served = await funds.company_funds(conn, "TSLA")

    assert (served.data.cusips, served.data.holders, served.sources) == (("88160R101",), (), ())


@pytest.mark.parametrize(
    ("text", "cik"),
    [
        ("Berkshire", BERKSHIRE),
        ("berkshire hathaway", BERKSHIRE),
        ("1350694", BRIDGEWATER),
        ("hathaway", BERKSHIRE),  # a word of the name
        ("RENAISSANCE TECHNOLOGIES", RENAISSANCE),  # EDGAR's name
    ],
)
async def test_a_manager_by_name_or_cik(conn: Conn, world: None, text: str, cik: int) -> None:
    assert (await funds.resolve_manager(conn, text)).manager_cik == cik


async def test_an_unclear_or_unknown_manager_says_which_there_are(conn: Conn, world: None) -> None:
    with pytest.raises(InvalidParameter, match="matches Berkshire Hathaway .*Bridgewater"):
        await funds.resolve_manager(conn, "b")
    with pytest.raises(NotFound, match="Curated managers: Berkshire Hathaway, Bridgewater"):
        await funds.resolve_manager(conn, "scion")
    with pytest.raises(NotFound, match="CIK 1649339 is not a curated manager"):
        await funds.resolve_manager(conn, "1649339")
