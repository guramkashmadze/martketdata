"""E7a-S3: the scale a press release table states (PLAN.md §9.9). Phrases taken from the
releases of the four-quarter backfill."""

from decimal import Decimal

import pytest

from marketdata.extract.units import BILLION, MILLION, THOUSAND, find_scale

ONE = Decimal(1)


@pytest.mark.parametrize(
    ("line", "money", "shares"),
    [
        ("(in millions, except per share amounts)", MILLION, MILLION),
        ("(In thousands)", THOUSAND, THOUSAND),
        ("(Dollars in Millions)", MILLION, MILLION),
        ("(Unaudited) (Millions of Dollars)", MILLION, MILLION),
        ("(In US $ millions, except percentages)", MILLION, MILLION),
        ("(U.S. $ in thousands)", THOUSAND, THOUSAND),
        ("Unaudited; Expressed in thousands, except per share amounts", THOUSAND, THOUSAND),
        ("$ millions Per Share $ millions Per Share", MILLION, MILLION),
        ("Dollars in billions, except per share data.", BILLION, BILLION),
        ("($000s omitted)", THOUSAND, THOUSAND),
        # Apple: money in millions, share counts in thousands.
        (
            "(In millions, except number of shares, which are reflected in thousands, and "
            "per-share amounts)",
            MILLION,
            THOUSAND,
        ),
        ("(In thousands, except share and per share amounts)", THOUSAND, ONE),
        ("(amounts in millions, except par value and share data)", MILLION, ONE),
        ("($ in millions, except earnings per share) Q3 FY26", MILLION, MILLION),
        ("(U.S. $ and shares in thousands, except per share data)", THOUSAND, THOUSAND),
        ("(In millions of dollars and shares except per share amounts)", MILLION, MILLION),
        ("Shares used in calculation (000's)", THOUSAND, THOUSAND),
    ],
)
def test_scales(line: str, money: Decimal, shares: Decimal) -> None:
    scale = find_scale([line])

    assert (scale.money, scale.shares) == (money, shares)


@pytest.mark.parametrize(
    "line",
    [
        "(2) Includes $3,347 million of invested assets classified as held for sale.",
        "Revenue grew 5% in the quarter",
        "Three Months Ended June 30,",
    ],
)
def test_lines_that_state_no_scale(line: str) -> None:
    assert find_scale([line]).money is None


def test_the_first_line_that_states_a_scale_wins_and_its_words_are_kept() -> None:
    scale = find_scale(
        [
            "CONDENSED CONSOLIDATED STATEMENT OF INCOME",
            "Dollars in millions (unless otherwise noted) Three Months Ended June 30,",
            "(in thousands)",
        ]
    )

    assert scale.money == MILLION
    assert scale.text == "Dollars in millions (unless otherwise noted)"
