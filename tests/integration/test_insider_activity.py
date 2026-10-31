"""E8-S5: a company's insider trades (§9.7, §10.2): per insider group, day, code and security,
shares summed, price weighted by shares, the filing's 10b5-1 box and the proof of each; totals
and a summary per insider over the window; purchases and sales by default. A 4/A's restated
lines are served from it, once."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import INSIDER_FORM4, seed
from conftest import connect_to, filing_routes, sec_serving

from marketdata.db.repositories import filings
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.form4 import Form4Ingest
from marketdata.normalize.filings import FilingRow
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.errors import InvalidParameter, NotApplicable
from marketdata.service.insiders import insider_activity

Conn = psycopg.AsyncConnection[Any]
CRWV = 1769628


@pytest.fixture
async def seeded(conn: Conn) -> Conn:
    await seed(conn)
    return conn


async def test_purchases_and_sales_are_grouped_with_their_proof(seeded: Conn) -> None:
    served = await insider_activity(seeded, "acme")

    activity = served.data
    assert (activity.ticker, activity.codes, activity.include_derivative) == (
        "ACME",
        ("P", "S"),
        False,
    )
    [sale] = activity.trades
    assert (sale.code, sale.code_label, sale.security, sale.shares) == (
        "S",
        "Sale (open market or private)",
        "Common Stock",
        Decimal("1500"),
    )
    # (1,000 x 50.25 + 500 x 51) / 1,500 shares
    assert (sale.average_price, sale.value, sale.unit) == (
        Decimal("50.5000"),
        Decimal("75750.00"),
        "USD",
    )
    assert (sale.holdings_after, sale.direct_indirect, sale.rule_10b5_1) == (
        Decimal("98500"),
        "D",
        True,
    )
    assert sale.footnotes == ("Sold under a Rule 10b5-1 plan adopted on 2026-03-02.",)
    [insider] = sale.insiders
    assert (insider.name, insider.roles, insider.officer_title) == (
        "Roe Richard",
        ("director", "officer"),
        "Chief Executive Officer",
    )
    [proof] = sale.proof.filings
    assert (proof.accession_no, proof.lines, proof.form) == (INSIDER_FORM4, (1, 2), "4")
    assert sale.proof.checks == (
        "issuer_matches_listing",
        "direction_matches_code",
        "price_reported",
    )
    [total] = activity.totals
    assert (total.code, total.trades, total.total_shares, total.total_value) == (
        "S",
        1,
        Decimal("1500"),
        Decimal("75750.00"),
    )
    assert [source.accession_no for source in served.sources] == [INSIDER_FORM4]
    assert served.quality_flags == ()


async def test_every_code_shows_the_gift_without_a_price_and_its_flag(seeded: Conn) -> None:
    served = await insider_activity(seeded, "ACME", codes=["all"])

    gift, sale = served.data.trades  # the same day: by code
    assert sale.code == "S"
    assert (gift.code, gift.code_label, gift.value, gift.average_price) == ("G", "Gift", None, None)
    assert gift.quality_flags == ("price_not_reported",)
    assert [flag.code for flag in served.quality_flags] == ["price_not_reported"]
    assert [(total.code, total.total_value) for total in served.data.totals] == [
        ("G", None),
        ("S", Decimal("75750.00")),
    ]
    summary = served.data.insiders
    assert {(item.code, item.trades) for item in summary} == {("S", 1), ("G", 1)}


async def test_trades_come_a_page_at_a_time(seeded: Conn) -> None:
    first = await insider_activity(seeded, "ACME", codes=["all"], limit=1)
    assert first.data.next_cursor is not None

    rest = await insider_activity(
        seeded, "ACME", codes=["all"], limit=1, cursor=first.data.next_cursor
    )

    assert [trade.code for trade in (*first.data.trades, *rest.data.trades)] == ["G", "S"]
    assert rest.data.next_cursor is None
    assert len(rest.data.totals) == 2  # totals cover the window, not the page


async def test_the_window_counts_back_from_today(seeded: Conn) -> None:
    later = await insider_activity(
        seeded, "ACME", days=5, today=datetime.now(UTC).date() + timedelta(days=1)
    )

    assert later.data.trades == () and later.data.totals == ()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"days": 0}, "days must be from 1 to 730"),
        ({"days": 731}, "days must be from 1 to 730"),
        ({"codes": ["PS"]}, "one letter each"),
        ({"codes": ["P", "7"]}, "got 7"),
    ],
)
async def test_bad_parameters_say_what_is_allowed(
    seeded: Conn, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(InvalidParameter, match=message):
        await insider_activity(seeded, "ACME", **options)


@pytest.mark.parametrize(
    ("ticker", "reason"),
    [
        ("FNDX", "A fund: insider Form 4 filings don't apply."),
        ("OUTC", "Not in the covered universe."),
    ],
)
async def test_a_company_insiders_dont_apply_to_gets_the_reason(
    seeded: Conn, ticker: str, reason: str
) -> None:
    with pytest.raises(NotApplicable, match=reason):
        await insider_activity(seeded, ticker)


async def test_a_foreign_issuer_applies(seeded: Conn) -> None:
    served = await insider_activity(seeded, "FPIX")

    assert served.data.trades == () and served.sources == ()


async def test_the_route_answers_the_envelope_and_refuses_a_wide_window(
    api: httpx2.AsyncClient, seeded: Conn, api_key: str
) -> None:
    headers = {"X-API-Key": api_key}

    ok = await api.get("/v1/companies/ACME/insiders", headers=headers)
    wide = await api.get("/v1/companies/ACME/insiders", params={"days": 731}, headers=headers)
    fund = await api.get("/v1/companies/FNDX/insiders", headers=headers)

    assert ok.status_code == 200 and ok.json()["data"]["trades"][0]["value"] == 75750.00
    assert (wide.status_code, wide.json()["error"]["code"]) == (400, "invalid_parameter")
    assert (fund.status_code, fund.json()["error"]["code"]) == (422, "not_applicable")


async def test_a_restated_filings_trades_are_served_once_from_the_4a(
    conn: Conn, tmp_path: Path
) -> None:
    """Magnetar's 4/A restates the 24 sales of its Form 4 of 2026-08-14: one trade, from it."""
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) "
        "values (%s, 'CoreWeave, Inc.', 'domestic', true)",
        [CRWV],
    )
    await conn.execute(
        "insert into securities (cik, ticker, security_type, valid_from) "
        "values (%s, 'CRWV', 'common', '2026-10-25')",
        [CRWV],
    )
    async with sec_serving(filing_routes()) as sec:
        ingest = Form4Ingest(
            RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn)), connect_to(conn)
        )
        for accession, form, filed, name in [
            ("0001104659-26-097435", "4", date(2026, 8, 14), "tm2623282-5_4seq1.xml"),
            ("0001104659-26-098473", "4/A", date(2026, 8, 18), "tm2623554-1_4aseq1.xml"),
        ]:
            row = FilingRow(accession, CRWV, form, filed, None, None, None, name, None, None)
            await filings.insert_new(conn, [row])
            await ingest.run(CRWV, accession)

    served = await insider_activity(conn, "CRWV", days=365, today=date(2026, 10, 31))

    [sale] = served.data.trades
    assert [insider.name for insider in sale.insiders] == [
        "Magnetar Financial LLC",
        "Magnetar Capital Partners LP",
        "Supernova Management LLC",
        "Snyderman David J.",
    ]
    [proof] = sale.proof.filings
    assert (proof.accession_no, proof.form, proof.lines) == (
        "0001104659-26-098473",
        "4/A",
        tuple(range(1, 25)),
    )
    assert sale.shares == Decimal("307131")  # the 24 sales of its funds
    assert [source.accession_no for source in served.sources] == ["0001104659-26-098473"]
