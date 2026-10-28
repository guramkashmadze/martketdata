"""E4b-S6: the release-vs-filing cross-check (D-028, PLAN.md §9.10), with synthetic facts for a
matching quarter, a quarter whose numbers changed between release and filing, a slot with no
comparable value, a filing not yet loaded, one listed without facts, and a release that
states no period end."""

from datetime import date
from decimal import Decimal

import pytest

from marketdata.normalize.companyfacts import XbrlFact
from marketdata.normalize.cross_check import (
    PeriodicFiling,
    ReleaseCheck,
    ReleaseSlots,
    check_releases,
)
from marketdata.verify.cross_check import (
    MATCHED,
    MISMATCH,
    NOT_COMPARABLE,
    PENDING,
    RELEASE_VS_FILING_MISMATCH,
    compare,
    flags_after,
)

M = Decimal(1_000_000)
TEN_Q = "0000000001-26-000020"  # the 10-Q for the quarter to 2026-06-30
RESTATED = "0000000001-27-000020"  # a year later, reporting the quarter again
Q1_10Q = "0000000001-26-000010"
Q3_10Q = "0000000001-26-000030"
TEN_K = "0000000001-27-000001"
Q2 = (date(2026, 4, 1), date(2026, 6, 30))
FILED = {
    Q1_10Q: date(2026, 5, 1),
    TEN_Q: date(2026, 8, 1),
    Q3_10Q: date(2026, 11, 1),
    TEN_K: date(2027, 2, 15),
    RESTATED: date(2027, 8, 1),
}


def _fact(
    concept: str,
    period: tuple[date, date],
    value: Decimal | int | str,
    accession: str = TEN_Q,
    *,
    unit: str = "USD",
) -> XbrlFact:
    return XbrlFact(
        cik=1,
        taxonomy="us-gaap",
        concept=concept,
        unit=unit,
        period_start=period[0],
        period_end=period[1],
        value=Decimal(value),
        fy=None,
        fp=None,
        form="10-K" if accession == TEN_K else "10-Q",
        frame=None,
        filed_at=FILED[accession],
        accession_no=accession,
    )


EPS = Decimal("1.25")
ACCEPTED = date(2026, 7, 28)


def _release(
    revenue: Decimal | None,
    eps: Decimal | None = EPS,
    *,
    period_end: date | None = Q2[1],
    label: str | None = "Three Months Ended June 30, 2026",
    accepted: date = ACCEPTED,
) -> ReleaseSlots:
    return ReleaseSlots(
        accession_no="0000000001-26-000015",
        accepted=accepted,
        period_end=period_end,
        period_label=label,
        values={"revenue": ("USD", revenue), "eps_diluted": ("USD/shares", eps)},
    )


QUARTER = [
    _fact("Revenues", Q2, 1_658 * M),
    _fact("EarningsPerShareDiluted", Q2, "1.25", unit="USD/shares"),
]
LISTED = [PeriodicFiling(TEN_Q, "10-Q", FILED[TEN_Q], Q2[1])]


def _check(
    release: ReleaseSlots,
    facts: list[XbrlFact],
    filings: list[PeriodicFiling] = LISTED,
) -> ReleaseCheck:
    [check] = check_releases([release], facts, filings, fiscal_year_end="1231")
    return check


def _statuses(check: ReleaseCheck) -> dict[str, str]:
    return {slot.slot: slot.status for slot in check.slots}


@pytest.mark.parametrize(
    ("unit", "release", "filing", "status"),
    [
        ("USD", "1658000000", "1658123456", MATCHED),
        ("USD", "995", "1000", MATCHED),  # 0.5% of the filing's value, inclusive
        ("USD", "994", "1000", MISMATCH),
        ("USD", "-836", "-836", MATCHED),
        ("USD/shares", "1.25", "1.26", MATCHED),  # within $0.01
        ("USD/shares", "1.25", "1.27", MISMATCH),
        ("USD", None, "100", NOT_COMPARABLE),
        ("USD", "100", None, NOT_COMPARABLE),
    ],
)
def test_the_tolerances_of_d028(
    unit: str, release: str | None, filing: str | None, status: str
) -> None:
    assert (
        compare(unit, Decimal(release) if release else None, Decimal(filing) if filing else None)
        == status
    )


def test_a_matching_quarter() -> None:
    check = _check(_release(Decimal(1_658) * M), QUARTER)
    [revenue, eps] = check.slots

    assert (check.quarter_end, check.period_from_filing) == (Q2[1], False)
    assert (revenue.status, revenue.filing_value, revenue.filing_accession) == (
        MATCHED,
        Decimal(1_658) * M,
        TEN_Q,
    )
    assert eps.status == MATCHED


def test_a_quarter_whose_numbers_changed_between_release_and_filing() -> None:
    # Rivian's release served six months as the quarter (E4a-S1 survey): 3,039 against the
    # 10-Q's 1,658.
    check = _check(_release(Decimal(3_039) * M, Decimal("-0.97")), QUARTER)

    assert _statuses(check) == {"revenue": MISMATCH, "eps_diluted": MISMATCH}
    assert check.slots[0].filing_value == Decimal(1_658) * M
    assert flags_after(("sign_change",), MISMATCH) == ("sign_change", RELEASE_VS_FILING_MISMATCH)
    assert flags_after(("sign_change", RELEASE_VS_FILING_MISMATCH), MATCHED) == ("sign_change",)


def test_a_slot_with_no_comparable_value() -> None:
    # The release's EPS failed its checks (no value); the 10-Q tags no revenue at all.
    check = _check(_release(Decimal(1_658) * M, None), QUARTER[1:])

    assert _statuses(check) == {"revenue": NOT_COMPARABLE, "eps_diluted": NOT_COMPARABLE}
    assert check.slots[1].filing_value == Decimal("1.25")  # kept for investigation


def test_the_release_is_compared_with_the_quarter_as_first_filed() -> None:
    # A year later the 10-Q restates the quarter: that is no error of the release's.
    facts = [*QUARTER, _fact("Revenues", Q2, 1_600 * M, RESTATED)]
    [revenue, _] = _check(_release(Decimal(1_658) * M), facts).slots

    assert (revenue.status, revenue.filing_accession) == (MATCHED, TEN_Q)


def test_a_fourth_quarter_is_the_10k_year_less_nine_months() -> None:
    facts = [
        _fact("Revenues", (date(2026, 1, 1), date(2026, 9, 30)), 4_900 * M, Q3_10Q),
        _fact("Revenues", (date(2026, 1, 1), date(2026, 12, 31)), 6_600 * M, TEN_K),
        _fact(
            "EarningsPerShareDiluted",
            (date(2026, 1, 1), date(2026, 12, 31)),
            "5.10",
            TEN_K,
            unit="USD/shares",
        ),
    ]
    release = _release(Decimal(1_700) * M, Decimal("1.40"), period_end=date(2026, 12, 31))
    check = _check(
        release, facts, [PeriodicFiling(TEN_K, "10-K", FILED[TEN_K], date(2026, 12, 31))]
    )

    assert _statuses(check) == {"revenue": MATCHED, "eps_diluted": NOT_COMPARABLE}
    assert check.slots[0].filing_accession == TEN_K  # the year less nine months, as filed


def test_pending_until_the_10q_is_listed() -> None:
    check = _check(_release(Decimal(1_658) * M), [], [])

    assert set(_statuses(check).values()) == {PENDING}
    assert check.note == "no 10-Q or 10-K for the quarter yet"


def test_a_10q_listed_without_facts_is_not_comparable() -> None:
    # KO's, V's and NEE's July 2026 10-Qs are in their submissions, not in companyfacts.
    check = _check(_release(Decimal(1_658) * M), [])

    assert set(_statuses(check).values()) == {NOT_COMPARABLE}
    assert TEN_Q in check.note


def test_a_release_without_a_period_end_takes_it_from_the_filing() -> None:
    # J&J's releases say "SECOND QUARTER 2026" with no day; the 10-Q's quarter ended 28 days
    # before the release, and it is the second quarter.
    release = _release(Decimal(1_658) * M, period_end=None, label="SECOND QUARTER 2026 Amount")
    check = _check(release, QUARTER)

    assert (check.quarter_end, check.period_from_filing) == (Q2[1], True)
    assert _statuses(check) == {"revenue": MATCHED, "eps_diluted": MATCHED}


def test_the_named_quarter_must_be_the_filings() -> None:
    # The release names the third quarter, but the only filing is for the second.
    release = _release(Decimal(1_658) * M, period_end=None, label="Third Quarter 2026")
    check = _check(release, QUARTER)

    assert check.quarter_end is None
    assert set(_statuses(check).values()) == {NOT_COMPARABLE}  # a later filing exists


def test_a_stated_end_a_few_days_off_the_filings_is_the_filings() -> None:
    # A 52/53-week quarter printed with its nominal date.
    check = _check(_release(Decimal(1_658) * M, period_end=date(2026, 7, 3)), QUARTER)

    assert check.quarter_end == Q2[1]
    assert _statuses(check)["revenue"] == MATCHED


def test_quarters_derived_across_filings_are_checked_too() -> None:
    # The quarter from the six months less the first: both filed by the 10-Q's day.
    facts = [
        _fact("Revenues", (date(2026, 1, 1), date(2026, 3, 31)), 1_000 * M, Q1_10Q),
        _fact("Revenues", (date(2026, 1, 1), Q2[1]), 2_658 * M),
    ]
    [revenue, _] = _check(_release(Decimal(1_658) * M), facts).slots

    assert (revenue.status, revenue.filing_value) == (MATCHED, Decimal(1_658) * M)
