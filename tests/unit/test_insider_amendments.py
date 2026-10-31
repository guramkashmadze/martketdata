"""E8-S3: which lines a 4/A, or a filing made twice, supersedes (PLAN.md §9.7), on the real cases
the survey of the universe's 56 amendments found: a 4/A that restates its filing, one that
corrects one line of ten, one that only attaches a power of attorney, one that corrects a
trade date, one that adds omitted trades, and an original filed twice."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from marketdata.normalize.form4 import Form4, Line, parse_form4
from marketdata.normalize.insiders import (
    GroupFiling,
    Supersession,
    group_supersessions,
    reparse_changes,
    supersession_changes,
)

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"
CRWV, GOOGL, NOW, ATI = 1769628, 1652044, 1065280, 1835632


def _form4(cik: int, accession: str, name: str) -> Form4:
    return parse_form4((FILINGS / str(cik) / accession / name).read_bytes(), accession)


def _filed(cik: int, accession: str, name: str, filed_at: date) -> GroupFiling:
    form4 = _form4(cik, accession, name)
    return GroupFiling(
        accession, form4.document_type, filed_at, form4.original_filed_at, form4.lines
    )


def _amendment(cik: int, accession: str, name: str, filed_at: date) -> GroupFiling:
    filing = _filed(cik, accession, name, filed_at)
    assert filing.form == "4/A"
    return filing


def test_a_4a_that_restates_its_filing_supersedes_every_line() -> None:
    """Magnetar's 4/A of 2026-08-18 repeats the 24 sales of its Form 4 of 2026-08-14; its Form 4
    of warrants the same day keeps its lines."""
    common = _filed(CRWV, "0001104659-26-097435", "tm2623282-5_4seq1.xml", date(2026, 8, 14))
    warrants = _filed(CRWV, "0001104659-26-097436", "tm2623282-6_4seq1.xml", date(2026, 8, 14))
    amendment = _amendment(
        CRWV, "0001104659-26-098473", "tm2623554-1_4aseq1.xml", date(2026, 8, 18)
    )

    decision = group_supersessions([common, warrants, amendment])

    assert decision.superseded == {
        (common.accession_no, n): Supersession(amendment.accession_no, n, "amendment")
        for n in range(1, 25)
    }
    assert decision.unmatched == ()


def test_a_4a_that_corrects_one_line_supersedes_only_that_line() -> None:
    """One trust sale's acquired-or-disposed code was wrong; the other nine sales stand."""
    original = _filed(CRWV, "0001415889-25-023219", "form4-08282025_080845.xml", date(2025, 8, 28))
    amendment = _amendment(
        CRWV, "0001415889-25-023333", "form4a-08302025_120842.xml", date(2025, 8, 29)
    )
    assert (len(original.lines), len(amendment.lines)) == (10, 1)

    decision = group_supersessions([original, amendment])

    assert decision.superseded == {
        (original.accession_no, 6): Supersession(amendment.accession_no, 1, "amendment")
    }


def test_a_4a_without_lines_supersedes_nothing() -> None:
    """Frances Arnold's 4/A attaches the power of attorney the Form 4 left out."""
    original = _filed(GOOGL, "0000950170-25-112319", "ownership.xml", date(2025, 8, 29))
    amendment = _amendment(GOOGL, "0000950170-25-112321", "ownership.xml", date(2025, 8, 29))

    decision = group_supersessions([original, amendment])

    assert decision.superseded == {} and decision.unmatched == ()


def test_a_corrected_trade_date_supersedes_the_line_with_the_wrong_date() -> None:
    """The Form 4 dated a sale June 30 instead of July 30, 2025."""
    original = _filed(GOOGL, "0000950170-25-101085", "ownership.xml", date(2025, 7, 31))
    amendment = _amendment(GOOGL, "0000950170-25-110753", "ownership.xml", date(2025, 8, 21))
    (wrong,) = original.lines
    (right,) = amendment.lines
    assert (wrong.trade_date, right.trade_date) == (date(2025, 6, 30), date(2025, 7, 30))

    decision = group_supersessions([original, amendment])

    assert decision.superseded == {
        (original.accession_no, 1): Supersession(amendment.accession_no, 1, "amendment")
    }


def test_a_4a_that_adds_omitted_trades_supersedes_nothing() -> None:
    """The 4/A adds a performance award's vesting the Form 4 of 2026-01-20 left out."""
    original = _filed(ATI, "0001628280-26-002747", "wk-form4_1768950915.xml", date(2026, 1, 20))
    amendment = _amendment(ATI, "0001193125-26-032163", "ownership.xml", date(2026, 1, 30))

    decision = group_supersessions([original, amendment])

    assert decision.superseded == {} and decision.unmatched == ()


def test_a_filing_made_twice_is_served_once_and_a_4a_adds_its_second_part() -> None:
    """ServiceNow's director filed the same Form 4 twice on 2025-06-02, then a 4/A with the
    second part of its sales."""
    first = _filed(NOW, "0001062993-25-010708", "form4.xml", date(2025, 6, 2))
    copy = _filed(NOW, "0001062993-25-010709", "form4.xml", date(2025, 6, 2))
    second_part = _amendment(NOW, "0001062993-25-011280", "form4a.xml", date(2025, 6, 9))

    decision = group_supersessions([copy, second_part, first])

    assert decision.superseded == {
        (copy.accession_no, n): Supersession(first.accession_no, n, "duplicate")
        for n in range(1, 31)
    }


def test_an_amendment_naming_no_stored_filing_is_unmatched() -> None:
    amendment = _amendment(GOOGL, "0000950170-25-110753", "ownership.xml", date(2025, 8, 21))

    decision = group_supersessions([amendment])

    assert decision.superseded == {} and decision.unmatched == (amendment.accession_no,)


def _sale(line_no: int, day: int, shares: str, owned: str) -> Line:
    return Line(
        line_no=line_no,
        is_derivative=False,
        security_title="Common Stock",
        trade_date=date(2026, 3, day),
        code="S",
        acquired_disposed="D",
        shares=Decimal(shares),
        price=Decimal("10"),
        owned_after=Decimal(owned),
        direct_indirect="D",
        nature_of_ownership=None,
        timeliness=None,
    )


def _filing(
    accession: str, day: int, *lines: Line, form: str = "4", amends: int = 0
) -> GroupFiling:
    return GroupFiling(
        accession, form, date(2026, 3, day), date(2026, 3, amends) if amends else None, lines
    )


def test_of_two_same_day_filings_like_the_4a_each_loses_only_the_line_it_corrects() -> None:
    """Two one-sale Form 4s the same day and a 4/A correcting one: which it restates in full
    isn't known, so only the corrected line is superseded."""
    morning = _filing("A-1", 5, _sale(1, 3, "100", "900"))
    afternoon = _filing("A-2", 5, _sale(1, 4, "50", "850"))
    amendment = _filing("A-3", 9, _sale(1, 4, "60", "840"), form="4/A", amends=5)

    decision = group_supersessions([morning, afternoon, amendment])

    assert decision.superseded == {("A-2", 1): Supersession("A-3", 1, "amendment")}


def test_a_later_4a_of_the_same_filing_supersedes_the_earlier_4a_too() -> None:
    original = _filing("B-1", 5, _sale(1, 3, "100", "900"))
    first_fix = _filing("B-2", 9, _sale(1, 3, "110", "890"), form="4/A", amends=5)
    second_fix = _filing("B-3", 12, _sale(1, 3, "120", "880"), form="4/A", amends=5)

    decision = group_supersessions([second_fix, original, first_fix])

    assert decision.superseded == {
        ("B-1", 1): Supersession("B-3", 1, "amendment"),
        ("B-2", 1): Supersession("B-3", 1, "amendment"),
    }


def test_a_served_trade_a_4a_corrects_is_a_change_with_old_and_new_shares() -> None:
    old, new = _sale(1, 3, "100", "900"), _sale(1, 4, "120", "880")
    lines = {("C-1", 1): old, ("C-2", 1): new}

    [change] = supersession_changes(
        lines, {("C-1", 1): None}, {("C-1", 1): Supersession("C-2", 1, "amendment")}
    )

    assert dict(change.entity_key) == {
        "accession_no": "C-1",
        "line_no": "1",
        "field": "shares",
        "served_from": "C-2",
    }
    assert (change.old_value, change.new_value, change.reason) == (
        Decimal("100"),
        Decimal("120"),
        "amendment",
    )
    assert change.detail == (
        "4/A C-2 line 1 corrects line 1 of C-1: trade date 2026-03-03 to 2026-03-04; shares 100 "
        "to 120; holdings after 900 to 880"
    )


def test_a_trade_restated_as_it_was_or_never_served_is_no_change() -> None:
    old = _sale(1, 3, "100", "900")
    restated = {("D-1", 1): Supersession("D-2", 1, "amendment")}

    same = supersession_changes({("D-1", 1): old, ("D-2", 1): old}, {("D-1", 1): None}, restated)
    new = supersession_changes({("D-1", 1): old, ("D-2", 1): old}, {}, restated)

    assert (same, new) == ([], [])


def test_a_line_dropped_by_a_restatement_and_a_duplicate_removed_are_changes() -> None:
    old = _sale(2, 3, "100", "900")
    lines = {("E-1", 2): old, ("E-3", 2): old}

    dropped, duplicate = supersession_changes(
        lines,
        {("E-1", 2): None, ("E-3", 2): None},
        {
            ("E-1", 2): Supersession("E-2", None, "amendment"),
            ("E-3", 2): Supersession("E-4", 2, "duplicate"),
        },
    )

    assert (dropped.old_value, dropped.new_value, dropped.reason) == (
        Decimal("100"),
        None,
        "amendment",
    )
    assert dropped.detail == "4/A E-2 restates E-1 without its line 2"
    assert (duplicate.reason, duplicate.new_value) == ("correction", None)
    assert duplicate.detail == (
        "E-3 repeats E-4, filed the same day: line 2 is served once, from E-4"
    )


def test_a_new_parser_version_that_reads_a_line_otherwise_is_a_correction() -> None:
    stored = _sale(1, 3, "100", "900")

    [change] = reparse_changes(
        "F-1",
        {1: stored},
        [replace(stored, price=Decimal("10.5"))],
        old_version="1",
        new_version="2",
    )

    assert (change.reason, change.old_value, change.new_value) == (
        "correction",
        Decimal("100"),
        Decimal("100"),
    )
    assert change.detail == "parser version 1 to 2: line 1 of F-1: price 10 to 10.5"
