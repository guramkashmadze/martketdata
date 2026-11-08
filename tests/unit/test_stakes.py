"""E16-S3: holders of 5% stakes from their Schedule 13D and 13G filings (PLAN.md §9.15): who
a filing belongs to, its share class, the latest filing served, exits, the checks and the changes
a run makes to served stakes. Cases drawn from the universe's 466 structured filings."""

from datetime import date
from decimal import Decimal

import pytest

from marketdata.normalize.schedule13 import Person
from marketdata.normalize.stakes import (
    FORM_DIFFERS,
    POWERS_EXCEED_AMOUNT,
    StakeFiling,
    by_accession,
    filing_checks,
    headline,
    holder_keys,
    holdings,
    is_exited,
    stake_changes,
    stake_of,
)


def _person(
    amount: int | str,
    percent: str,
    *,
    position: int = 1,
    name: str = "Holder",
    cik: int | None = None,
    sole_voting: int | None = None,
    shared_voting: int | None = None,
    sole_dispositive: int | None = None,
    shared_dispositive: int | None = None,
) -> Person:
    def number(value: int | None) -> Decimal | None:
        return Decimal(value) if value is not None else None

    return Person(
        position=position,
        name=name,
        cik=cik,
        types=("IA",),
        citizenship="DE",
        amount=Decimal(amount),
        percent=Decimal(percent),
        sole_voting=number(sole_voting),
        shared_voting=number(shared_voting),
        sole_dispositive=number(sole_dispositive),
        shared_dispositive=number(shared_dispositive),
        member_of_group=None,
    )


def _filing(
    accession: str,
    filed: str,
    *,
    filer: int = 100,
    form: str = "SCHEDULE 13G/A",
    previous: str | None = None,
    cusip: str = "007903107",
    percent: str = "7.5",
    amount: int = 1000,
    ceased: bool | None = None,
    persons: tuple[Person, ...] | None = None,
    version: str = "1",
) -> StakeFiling:
    return StakeFiling(
        accession_no=accession,
        listing_cik=2488,
        form=form,
        filed_at=date.fromisoformat(filed),
        filer_cik=filer,
        previous_accession=previous,
        cusips=(cusip,),
        ceased_5pct=ceased,
        persons=persons or (_person(amount, percent),),
        extractor_version=version,
    )


def test_the_holders_amount_is_the_largest_one_person_states_never_a_sum() -> None:
    parent = _person(500, "5.5", position=1, name="Parent Inc")
    subsidiary = _person(500, "5.5", position=2, name="Adviser LLC")
    trust = _person(300, "3.3", position=3, name="Trust")

    assert headline([parent, subsidiary, trust]) is parent  # the first of equals
    assert headline([trust, _person(900, "9.9", position=4)]).amount == 900


@pytest.mark.parametrize(
    ("ceased", "percents", "exited"),
    [
        (True, ["7.5"], True),  # item 5 ticked, whatever the percent
        (None, ["4.9", "4.9"], True),  # Fresenius on Humacyte, 2026-08-18
        (None, ["0.97", "0.81", "0"], True),  # the Schmidt family on Alphabet
        (False, ["5"], False),  # State Street on Nike: 5 as filed, item 5 unticked
        (None, ["5.0"], False),  # rounded: the SEC's threshold is more than 5%
        (None, ["0.2", "44.9"], False),  # one person above 5% keeps the holder
    ],
)
def test_a_holder_exits_on_item_5_or_when_every_person_is_below_5_percent(
    ceased: bool | None, percents: list[str], exited: bool
) -> None:
    persons = [_person(10, percent, position=n) for n, percent in enumerate(percents, start=1)]

    assert is_exited(ceased, persons) is exited


def test_a_stake_names_its_first_reporting_person() -> None:
    filing = _filing(
        "0001104659-26-098152",
        "2026-08-18",
        persons=(
            _person(13603235, "4.9", position=1, name="Fresenius Medical Care Holdings, Inc."),
            _person(13603235, "4.9", position=2, name="Fresenius Medical Care AG"),
        ),
    )

    stake = stake_of(filing)

    assert (stake.holder, stake.amount, stake.percent, stake.exited) == (
        "Fresenius Medical Care Holdings, Inc.",
        Decimal(13603235),
        Decimal("4.9"),
        True,
    )


def test_filings_link_by_filer_person_cik_and_previous_accession() -> None:
    fresenius_holdings = _filing(
        "0001104659-26-033489",
        "2026-03-24",
        filer=42872,
        persons=(_person(10, "8.4", cik=42872), _person(10, "8.4", position=2, cik=1333141)),
    )
    fresenius_ag = _filing(  # the parent filed this one, both persons named
        "0001104659-26-002371",
        "2026-01-09",
        filer=1333141,
        persons=(_person(10, "9.5", cik=1333141), _person(10, "9.5", position=2, cik=42872)),
    )
    frontier_old = _filing("0001172661-25-003292", "2025-05-14", filer=1216488)
    frontier_new = _filing(  # under another CIK, naming the filing before it
        "0001172661-26-003511", "2026-02-13", filer=351173, previous="0001172661-25-003292"
    )
    unrelated = _filing("0002012383-26-003063", "2026-07-30", filer=2012383)

    keys = holder_keys([fresenius_holdings, fresenius_ag, frontier_old, frontier_new, unrelated])

    assert keys["0001104659-26-033489"] == keys["0001104659-26-002371"] == "0001104659-26-002371"
    assert keys["0001172661-26-003511"] == keys["0001172661-25-003292"] == "0001172661-25-003292"
    assert keys["0002012383-26-003063"] == "0002012383-26-003063"


def test_a_previous_accession_not_stored_links_nothing() -> None:
    one = _filing("0000000001-25-000001", "2025-02-01", filer=1, previous="0000000009-11-000001")
    other = _filing("0000000002-25-000001", "2025-02-01", filer=2, previous="0000000009-11-000001")

    keys = holder_keys([one, other])

    assert keys[one.accession_no] != keys[other.accession_no]


def test_the_latest_filing_of_a_holder_serves_whatever_its_form() -> None:
    first = _filing("0000000100-25-000001", "2025-02-01", form="SCHEDULE 13G", percent="5.5")
    active = _filing("0000000100-25-000009", "2025-11-05", form="SCHEDULE 13D", percent="8.2")
    passive = _filing("0000000100-26-000002", "2026-02-10", form="SCHEDULE 13G", percent="7.1")

    found = holdings([active, passive, first], {}, "AMD")

    (holding,) = found.holdings
    assert (holding.accession_no, holding.class_key) == (passive.accession_no, "AMD")
    assert holding.superseded == (active.accession_no, first.accession_no)  # newest first
    assert found.serving == {
        first.accession_no: passive.accession_no,
        active.accession_no: passive.accession_no,
        passive.accession_no: passive.accession_no,
    }


def test_two_filings_of_one_day_are_ordered_by_accession_number() -> None:
    earlier = _filing("0000000100-26-000001", "2026-02-10", percent="6.0")
    later = _filing("0000000100-26-000002", "2026-02-10", percent="6.5")

    (holding,) = holdings([later, earlier], {}, "AMD").holdings

    assert holding.accession_no == later.accession_no


def test_a_class_the_cusip_map_knows_is_a_holding_of_its_own() -> None:
    # Vanguard Capital Management filed Alphabet's Class C and Class A apart, the same day
    class_c = _filing("0002100119-26-000137", "2026-04-29", filer=2100119, cusip="02079K107")
    class_a = _filing("0002100119-26-000161", "2026-04-29", filer=2100119, cusip="02079K305")
    classes = {"02079K107": "GOOG", "02079K305": "GOOGL"}

    found = holdings([class_c, class_a], classes, "GOOG")

    assert sorted((h.class_key, h.accession_no) for h in found.holdings) == [
        ("GOOG", class_c.accession_no),
        ("GOOGL", class_a.accession_no),
    ]


def test_a_cusip_the_map_doesnt_know_counts_as_the_primary_class() -> None:
    # BlackRock on fuboTV: the CUSIP before the 2025 reverse split, then the new one
    before = _filing("0002012383-25-001477", "2025-02-05", filer=2012383, cusip="35953D104")
    after = _filing("0002012383-26-001448", "2026-04-24", filer=2012383, cusip="35953D401")

    (holding,) = holdings([before, after], {"35953D401": "FUBO"}, "FUBO").holdings

    assert (holding.accession_no, holding.superseded) == (
        after.accession_no,
        (before.accession_no,),
    )


def test_a_holder_exits_from_its_latest_filing() -> None:
    held = _filing("0000102909-25-000001", "2025-02-13", filer=102909, percent="8.9")
    exit_ = _filing("0000102909-26-000540", "2026-03-26", filer=102909, percent="0", ceased=True)

    (holding,) = holdings([held, exit_], {}, "AMD").holdings

    assert holding.accession_no == exit_.accession_no
    assert stake_of(exit_).exited


def test_a_first_run_changes_nothing_served() -> None:
    one = _filing("0000000100-25-000001", "2025-02-01")
    two = _filing("0000000100-26-000001", "2026-02-01", amount=2000, percent="9.0")
    after = holdings([one, two], {}, "AMD")

    assert stake_changes(holdings([], {}, "AMD"), after, {}, by_accession([one, two])) == []


def test_a_later_filing_that_replaces_a_served_stake_is_an_amendment() -> None:
    served = _filing("0000000100-25-000001", "2025-02-01", amount=1000, percent="7.5")
    later = _filing("0000000100-26-000001", "2026-02-01", amount=400, percent="3.0")
    before = holdings([served], {}, "AMD")
    after = holdings([served, later], {}, "AMD")

    (change,) = stake_changes(before, after, by_accession([served]), by_accession([served, later]))

    assert change.entity_key == {
        "accession_no": served.accession_no,
        "served_from": later.accession_no,
    }
    assert (change.old_value, change.new_value, change.reason) == (
        Decimal(1000),
        Decimal(400),
        "amendment",
    )
    assert change.detail == (
        "SCHEDULE 13G/A 0000000100-26-000001 filed 2026-02-01 replaces SCHEDULE 13G/A "
        "0000000100-25-000001 filed 2025-02-01: 1,000 shares (7.5% of the class) to 400 shares "
        "(3.0% of the class, exited)"
    )


def test_a_later_filing_with_the_same_stake_changes_nothing_served() -> None:
    served = _filing("0000000100-25-000001", "2025-02-01")
    same = _filing("0000000100-26-000001", "2026-02-01")

    changes = stake_changes(
        holdings([served], {}, "AMD"),
        holdings([served, same], {}, "AMD"),
        by_accession([served]),
        by_accession([served, same]),
    )

    assert changes == []


def test_a_new_parser_version_that_reads_a_served_filing_otherwise_is_a_correction() -> None:
    old = _filing("0000000100-25-000001", "2025-02-01", amount=1000, version="1")
    new = _filing("0000000100-25-000001", "2025-02-01", amount=1100, version="2")

    (change,) = stake_changes(
        holdings([old], {}, "AMD"),
        holdings([new], {}, "AMD"),
        by_accession([old]),
        by_accession([new]),
    )

    assert (change.reason, change.old_value, change.new_value) == (
        "correction",
        Decimal(1000),
        Decimal(1100),
    )
    assert change.detail is not None and change.detail.startswith("parser version 1 to 2: ")


def test_an_older_filing_stored_late_was_never_served() -> None:
    newer = _filing("0000000100-26-000001", "2026-02-01", amount=2000)
    older = _filing("0000000100-25-000001", "2025-02-01", amount=1000)

    changes = stake_changes(
        holdings([newer], {}, "AMD"),
        holdings([newer, older], {}, "AMD"),
        by_accession([newer]),
        by_accession([newer, older]),
    )

    assert changes == []


def test_the_checks_a_filing_passes_and_the_flags_of_those_it_fails() -> None:
    clean = [_person(100, "7.5", sole_voting=60, shared_voting=40, sole_dispositive=100)]
    # Elon Musk's 13G on Tesla states the same shares as sole and shared power
    doubled = [_person(100, "20.1", sole_voting=100, shared_voting=100, sole_dispositive=100)]

    assert filing_checks("SCHEDULE 13G", "SCHEDULE 13G", clean) == (
        (
            "issuer_matches_listing",
            "form_matches_listing",
            "percent_in_range",
            "powers_within_amount",
        ),
        (),
    )
    passed, flags = filing_checks("SCHEDULE 13G", "SCHEDULE 13G/A", doubled)
    assert flags == (FORM_DIFFERS, POWERS_EXCEED_AMOUNT)
    assert "percent_in_range" in passed


def test_a_percent_out_of_range_is_flagged() -> None:
    _, flags = filing_checks("SCHEDULE 13D", "SCHEDULE 13D", [_person(1, "101")])

    assert flags == ("percent_out_of_range",)
