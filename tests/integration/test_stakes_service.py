"""E16-S5: a company's 5% holders from its stored 13D and 13G filings (PLAN.md §9.15): each
holder's latest filing about each class, by name, those below 5% as exited, older filings as
links; share classes told apart by the CUSIP map; a changed stake served in the change feed with
the stake as served now."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from api_seed import (
    ACME,
    OLD_13G,
    STAKE_13D,
    STAKE_13G,
    STAKE_13G_BEFORE,
    seed,
)
from fund_world import link
from stake_world import (
    ALPHABET,
    AMD,
    FRESENIUS_AUGUST,
    FRESENIUS_JULY,
    HUMACYTE,
    SCHMIDT,
    VANGUARD_AMD,
    VCM_AMD,
    VCM_GOOG,
    VCM_GOOGL,
    ingest,
    list_filing,
)

from marketdata.sec import urls
from marketdata.service.changes import changes_feed, get_change, proof_path
from marketdata.service.errors import NotApplicable
from marketdata.service.proof import stake_filing_proof
from marketdata.service.stakes import ABOUT_STAKES, major_holders

Conn = psycopg.AsyncConnection[Any]


async def _company(conn: Conn, cik: int, name: str, ticker: str, filer: str = "domestic") -> None:
    await conn.execute(
        "insert into companies (cik, name, filer_type, in_universe) values (%s, %s, %s, true) "
        "on conflict (cik) do nothing",
        [cik, name, filer],
    )
    await conn.execute(
        "insert into securities (cik, ticker, security_type, valid_from) "
        "values (%s, %s, 'common', '2020-01-01') on conflict do nothing",
        [cik, ticker],
    )


async def test_holders_by_name_with_exits_and_older_filings_as_links(conn: Conn) -> None:
    await seed(conn)

    served = await major_holders(conn, "acme")

    data = served.data
    assert (data.ticker, data.cik, data.note) == ("ACME", ACME, ABOUT_STAKES)
    [held] = data.holders
    assert (held.holder, held.form, held.status, held.ticker) == (
        "Big Index Advisers",
        "SCHEDULE 13G/A",
        "held",
        "ACME",  # its CUSIP is ACME's in the CUSIP map
    )
    assert (held.shares, held.percent, held.unit, held.filings) == (
        Decimal(12500000),
        Decimal("6.25"),
        "shares",
        2,
    )
    assert held.proof.accession_no == STAKE_13G
    assert held.proof.supersedes == (STAKE_13G_BEFORE,)
    assert held.proof.filing_url == urls.filing_folder(ACME, STAKE_13G)
    assert held.proof.checks[0] == "issuer_matches_listing"
    [exited] = data.exited
    assert (exited.holder, exited.status, exited.schedule) == (
        "Activist Partners LP",
        "exited",
        "13D",
    )
    assert [person.cik for person in exited.reporting_persons] == [9200, 9201]
    assert exited.purpose is not None and exited.purpose.startswith("The Reporting Persons sold")
    assert [(f.accession_no, f.form, f.quality_flags) for f in data.older_filings] == [
        (OLD_13G, "SC 13G", ("not_parsed",))
    ]
    assert data.more_older_filings is False
    assert [source.accession_no for source in served.sources] == [STAKE_13D, STAKE_13G]
    assert served.as_of is not None


async def test_a_fund_in_the_universe_applies_and_a_company_outside_doesnt(conn: Conn) -> None:
    await seed(conn)

    fund = await major_holders(conn, "FNDX")

    assert (fund.data.holders, fund.data.exited, fund.data.older_filings) == ((), (), ())
    with pytest.raises(NotApplicable, match="Not in the covered universe"):
        await major_holders(conn, "OUTC")


async def test_amd_has_vanguard_capital_management_and_the_vanguard_groups_exit(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, AMD, "ADVANCED MICRO DEVICES INC", "AMD")
    for listed in (VANGUARD_AMD, VCM_AMD):
        await list_filing(conn, listed)
        await ingest(conn, tmp_path, listed)

    data = (await major_holders(conn, "AMD")).data

    [held] = data.holders
    assert (held.holder, held.shares, held.percent, held.event_date.isoformat()) == (
        "Vanguard Capital Management",
        Decimal(122092206),
        Decimal("7.48"),
        "2026-03-31",
    )
    assert held.proof.accession_no == VCM_AMD.accession_no
    [exited] = data.exited
    assert (exited.holder, exited.shares, exited.ceased_5pct) == (
        "The Vanguard Group",
        Decimal(0),
        True,
    )


async def test_two_classes_the_cusip_map_knows_are_two_holdings(conn: Conn, tmp_path: Path) -> None:
    await _company(conn, ALPHABET, "Alphabet Inc.", "GOOG")
    await link(conn, "02079K107", "GOOG", ALPHABET)
    await link(conn, "02079K305", "GOOGL", ALPHABET)
    for listed in (VCM_GOOG, VCM_GOOGL, SCHMIDT):
        await list_filing(conn, listed)
        await ingest(conn, tmp_path, listed)

    data = (await major_holders(conn, "GOOGL")).data

    assert [(h.holder, h.ticker, h.percent) for h in data.holders] == [
        ("Vanguard Capital Management", "GOOG", Decimal("6.41")),
        ("Vanguard Capital Management", "GOOGL", Decimal("7.48")),
    ]
    [schmidt] = data.exited  # every person below 5%
    assert (schmidt.holder, schmidt.ticker) == ("Eric E. Schmidt", "GOOGL")
    assert len(schmidt.reporting_persons) > 1
    assert schmidt.shares == Decimal(56921959)  # the largest one person states, not a sum


async def test_a_changed_stake_is_served_in_the_feed_as_served_now(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, HUMACYTE, "HUMACYTE, INC.", "HUMA")
    for listed in (FRESENIUS_JULY, FRESENIUS_AUGUST):
        await list_filing(conn, listed)
        await ingest(conn, tmp_path, listed)

    served = await changes_feed(conn, ticker="HUMA")

    [change] = served.data.changes
    assert (change.entity, change.ticker, change.reason, change.field) == (
        "stake_holding",
        "HUMA",
        "amendment",
        "shares",
    )
    assert (change.accession_no, change.served_from) == (
        FRESENIUS_JULY.accession_no,
        FRESENIUS_AUGUST.accession_no,
    )
    assert (change.old_value, change.new_value, change.value, change.unit) == (
        Decimal("18312735.00"),
        Decimal("13603235.00"),
        Decimal("13603235.00"),
        "shares",
    )
    assert change.proof is not None and change.proof.accession_no == FRESENIUS_AUGUST.accession_no
    assert [source.accession_no for source in served.sources] == [FRESENIUS_AUGUST.accession_no]
    assert proof_path(change) == f"/proof/stakes/{FRESENIUS_JULY.accession_no}"
    one = await get_change(conn, change.change_id)
    assert one.data == change


async def test_a_proof_page_shows_its_place_among_the_holders_filings(
    conn: Conn, tmp_path: Path
) -> None:
    await _company(conn, HUMACYTE, "HUMACYTE, INC.", "HUMA")
    for listed in (FRESENIUS_JULY, FRESENIUS_AUGUST):
        await list_filing(conn, listed)
        await ingest(conn, tmp_path, listed)

    july = await stake_filing_proof(conn, FRESENIUS_JULY.accession_no)
    august = await stake_filing_proof(conn, FRESENIUS_AUGUST.accession_no)

    assert (july.served, july.exited, august.served, august.exited) == (False, False, True, True)
    assert july.served_by is not None
    assert july.served_by.accession_no == FRESENIUS_AUGUST.accession_no
    assert [ref.accession_no for ref in august.holder_filings] == [
        FRESENIUS_AUGUST.accession_no,
        FRESENIUS_JULY.accession_no,
    ]
    assert [entry.reason for entry in july.history] == ["amendment"]
    assert [person.cik for person in august.persons] == [42872, 1333141]
    assert august.checks["issuer_matches_listing"].startswith("the filing's issuer is")
