"""E9-S4: a quarter's positions from its reports (§9.8): amendments in filing order, lines merged
by CUSIP and put or call, weights of the long equity positions, and change types from the
quarter before, split-adjusted when holders confirm a split."""

import gzip
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from marketdata.normalize.form13f import parse_information_table
from marketdata.normalize.holdings import (
    SHARE_TYPES_DIFFER,
    FiledLine,
    HeldBoth,
    PeriodFiling,
    backfill_start,
    changes,
    lines_in_force,
    parse_quarter,
    positions,
    quarter_end,
    quarter_label,
    quarters_before,
    split_ratio,
)

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"


def _filing(
    cik: int, accession: str, table: str, form: str, filed: date, kind: str | None = None
) -> PeriodFiling:
    data = (FILINGS / f"{cik}/{accession}/{table}").read_bytes()
    if table.endswith(".gz"):
        data = gzip.decompress(data)
    lines = tuple(
        FiledLine(
            accession,
            h.line_no,
            h.cusip,
            h.put_call,
            h.share_type,
            h.name_of_issuer,
            h.title_of_class,
            h.figi,
            h.shares,
            h.value,
            h.value,
            h.other_managers,
        )
        for h in parse_information_table(data)
    )
    return PeriodFiling(accession, form, filed, kind, lines)


HIMALAYA = _filing(1709323, "0002043585-26-000011", "13fhciq425.xml", "13F-HR", date(2026, 2, 17))
RESTATED = _filing(
    1709323,
    "0002043585-26-000012",
    "13fhciq425v2.xml",
    "13F-HR/A",
    date(2026, 2, 19),
    "RESTATEMENT",
)
VALUEACT = _filing(
    1418814, "0001418812-24-000021", "vac13f111424.xml", "13F-HR", date(2024, 11, 14)
)
ADDED = _filing(
    1418814,
    "0001418812-24-000022",
    "vac13f111524.xml",
    "13F-HR/A",
    date(2024, 11, 15),
    "NEW HOLDINGS",
)


def test_a_restatement_replaces_the_report_whatever_order_they_come_in() -> None:
    for filings in ([HIMALAYA, RESTATED], [RESTATED, HIMALAYA]):
        in_force = lines_in_force(filings)

        assert {line.accession_no for line in in_force.lines} == {RESTATED.accession_no}
        shares = {line.cusip: line.shares for line in in_force.lines}
        assert shares["02079K305"] == Decimal("2543300")  # Alphabet class A, corrected


def test_new_holdings_add_lines_and_a_repeated_report_counts_once() -> None:
    in_force = lines_in_force([VALUEACT, ADDED])

    assert len(in_force.lines) == 11  # the report's 10 and Toast
    assert [(line.accession_no, line.cusip) for line in in_force.lines][-1] == (
        ADDED.accession_no,
        "888787108",
    )
    assert len(in_force.repeated) == 10


def _line(accession: str, cusip: str, shares: str, value: str, **other: str | None) -> FiledLine:
    return FiledLine(
        accession_no=accession,
        line_no=int(other.get("line_no") or 1),
        cusip=cusip,
        put_call=other.get("put_call"),
        share_type=str(other.get("share_type") or "SH"),
        name_of_issuer=other.get("name", "ISSUER"),
        title_of_class="COM",
        figi=None,
        shares=Decimal(shares),
        value_as_filed=Decimal(value),
        value_usd=Decimal(value),
        other_managers=other.get("other_managers"),
    )


def test_berkshires_q3_2023_report_restatement_and_new_holdings() -> None:
    report = PeriodFiling(
        "a", "13F-HR", date(2023, 11, 14), None, (_line("a", "037833100", "1", "1"),)
    )
    restated = PeriodFiling(
        "b", "13F-HR/A", date(2023, 11, 16), "RESTATEMENT", (_line("b", "037833100", "2", "2"),)
    )
    chubb = PeriodFiling(
        "c", "13F-HR/A", date(2024, 5, 15), "NEW HOLDINGS", (_line("c", "H1467J104", "3", "3"),)
    )

    in_force = lines_in_force([chubb, restated, report])

    assert [(line.accession_no, line.cusip) for line in in_force.lines] == [
        ("b", "037833100"),
        ("c", "H1467J104"),
    ]


def test_lines_merge_by_cusip_and_put_or_call_and_weights_leave_options_and_principal_out() -> None:
    lines = (
        _line("a", "02005N100", "12561737", "577211815", other_managers="4"),
        _line("a", "02005N100", "2803875", "128838056", line_no="2", other_managers="2,4,11"),
        _line("a", "037833100", "1000", "250000", line_no="3"),
        _line("a", "037833100", "500", "9000", line_no="4", put_call="CALL"),
        _line("a", "912828XX9", "1000000", "990000", line_no="5", share_type="PRN"),
    )
    report = PeriodFiling("a", "13F-HR", date(2026, 8, 14), None, lines)

    merged = positions(lines_in_force([report]))

    ally, treasury, apple, call = merged  # largest value first
    assert (ally.key, ally.shares, ally.value_usd, ally.lines) == (
        ("02005N100", None),
        Decimal("15365612"),
        Decimal("706049871"),
        2,
    )
    assert (call.key, call.weight_pct, treasury.share_type, treasury.weight_pct) == (
        ("037833100", "CALL"),
        None,
        "PRN",
        None,
    )
    assert ally.weight_pct is not None and apple.weight_pct is not None
    assert ally.weight_pct + apple.weight_pct == Decimal("100.0000")
    assert apple.weight_pct == Decimal("0.0354")  # 250,000 of 706,299,871


def test_one_cusip_filed_as_shares_and_principal_is_flagged() -> None:
    lines = (
        _line("a", "88160RAJ0", "100", "50000"),
        _line("a", "88160RAJ0", "100000", "120000", line_no="2", share_type="PRN"),
    )

    [position] = positions(
        lines_in_force([PeriodFiling("a", "13F-HR", date(2026, 8, 14), None, lines)])
    )

    assert (position.share_type, position.quality_flags) == ("PRN", (SHARE_TYPES_DIFFER,))


def test_change_types_follow_the_one_percent_band() -> None:
    before = {
        ("a", None): Decimal("1000"),
        ("b", None): Decimal("1000"),
        ("c", None): Decimal("1000"),
        ("d", None): Decimal("1000"),
        ("e", None): Decimal("1000"),
    }
    now = {
        ("a", None): Decimal("1010"),  # exactly +1%: unchanged
        ("b", None): Decimal("1011"),  # +1.1%: added
        ("c", None): Decimal("989"),  # -1.1%: trimmed
        ("d", None): Decimal("990"),  # exactly -1%: unchanged
        ("f", None): Decimal("50"),
    }

    found = changes(now, before)

    assert {key[0]: change.change_type for key, change in found.items()} == {
        "a": "unchanged",
        "b": "added",
        "c": "trimmed",
        "d": "unchanged",
        "e": "exited",
        "f": "new",
    }
    exited, new = found[("e", None)], found[("f", None)]
    assert (exited.shares, exited.previous_shares, exited.shares_change) == (
        Decimal(0),
        Decimal("1000"),
        Decimal("-1000"),
    )
    assert (new.previous_shares, new.shares_change) == (None, Decimal("50"))


def test_a_confirmed_split_compares_shares_in_new_shares() -> None:
    netflix = "64110L106"
    before = {(netflix, None): Decimal("100000")}
    now = {(netflix, None): Decimal("1000000")}

    plain = changes(now, before)[(netflix, None)]
    split = changes(now, before, {netflix: Fraction(10)})[(netflix, None)]

    assert plain.change_type == "added"
    assert (split.change_type, split.shares_change, split.split_ratio) == (
        "unchanged",
        Decimal("0"),
        Fraction(10),
    )


def _held(cik: int, before: str, after: str, price_before: str, price_after: str) -> HeldBoth:
    return HeldBoth(
        cik,
        Decimal(before),
        Decimal(before) * Decimal(price_before),
        Decimal(after),
        Decimal(after) * Decimal(price_after),
    )


def test_a_split_needs_two_holders_and_the_price_moving_by_the_inverse_ratio() -> None:
    # Netflix, 10 for 1 on 2025-11-14: $1,200 a share at Q3's end, $95 at Q4's.
    two = [_held(1, "1000", "10000", "1200", "95"), _held(2, "250", "2500", "1200", "95")]
    traded = [*two, _held(3, "400", "5000", "1200", "95")]

    assert split_ratio(two) == Fraction(10)
    assert split_ratio(traded) == Fraction(10)
    assert split_ratio(two[:1]) is None  # one manager: a trade
    assert (
        split_ratio([_held(1, "1000", "10000", "100", "98"), _held(2, "5", "50", "100", "98")])
        is None
    )
    reverse = [_held(1, "1000", "100", "2", "21"), _held(2, "30", "3", "2", "19")]
    assert split_ratio(reverse) == Fraction(1, 10)


def test_quarters_and_the_backfill_window() -> None:
    assert quarter_end(date(2026, 11, 1)) == date(2026, 9, 30)
    assert quarter_end(date(2026, 3, 31)) == date(2026, 3, 31)
    assert quarter_end(date(2026, 1, 15)) == date(2025, 12, 31)
    assert quarters_before(date(2026, 9, 30), 8) == date(2024, 9, 30)
    # Eight quarters to Q3 2026, and Q3 2024 before them so Q4 2024 has change types.
    assert backfill_start(date(2026, 11, 1), 8) == date(2024, 10, 1)
    assert [parse_quarter(text) for text in ("2026Q2", "2025-q4", "2026 Q1", "2026-06-30")] == [
        date(2026, 6, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
        date(2026, 6, 30),
    ]
    assert quarter_label(date(2025, 12, 31)) == "2025Q4"
    for text in ("2026Q5", "2026-06-29", "Q2"):
        with pytest.raises(ValueError):
            parse_quarter(text)
