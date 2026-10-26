"""E7a-S4: verification checks (PLAN.md §9.9), independent of the extractor (D-015)."""

from decimal import Decimal

import pytest

from marketdata.verify.number_check import IN_SOURCE, SCALE, Claim, check_claim, read_number
from marketdata.verify.sanity import change_problem, eps_problem, revenue_problem

TEXT = "\n".join(
    [
        "Apple reports third quarter results",
        "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |",
        "| Diluted | $ 2.93 |  | $ 2.82 |  |",
        "| Other income/(expense), net | 572 | (171) | 670 | (698) |",
    ]
)
M = Decimal(1_000_000)


def _claim(cell: str, value: Decimal, quote: str, scale: Decimal = M) -> Claim:
    return Claim("revenue", value, cell, scale, quote, "ex991.htm")


def test_a_value_in_its_row_with_the_right_scale_passes() -> None:
    quote = "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |"

    assert check_claim(_claim("109,417", Decimal(109_417) * M, quote), TEXT) == []


def test_a_cell_after_an_empty_cell_is_in_its_row() -> None:
    quote = "| Diluted | $ 2.93 |  | $ 2.82 |  |"

    assert check_claim(_claim("$ 2.82", Decimal("2.82"), quote, Decimal(1)), TEXT) == []


def test_a_quote_that_is_not_a_line_of_the_text_fails() -> None:
    quote = "| Total net sales | 109,417 | 94,036 |"

    [problem] = check_claim(_claim("109,417", Decimal(109_417) * M, quote), TEXT)

    assert problem.check == IN_SOURCE


def test_a_cell_that_is_not_in_its_row_fails() -> None:
    quote = "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |"

    [problem] = check_claim(_claim("109,418", Decimal(109_418) * M, quote), TEXT)

    assert problem.check == IN_SOURCE


def test_the_label_is_not_a_value_cell() -> None:
    quote = "| Other income/(expense), net | 572 | (171) | 670 | (698) |"

    problems = check_claim(_claim("Other income/(expense), net", Decimal(1), quote), TEXT)

    assert {problem.check for problem in problems} == {IN_SOURCE, SCALE}


def test_a_wrong_scale_fails() -> None:
    quote = "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |"
    claim = _claim("109,417", Decimal(109_417) * 1000, quote)  # read as thousands

    [problem] = check_claim(claim, TEXT)

    assert problem.check == SCALE
    assert "is 109417000000, not 109417000" in problem.message


@pytest.mark.parametrize(
    ("cell", "number"),
    [
        ("109,417", "109417"),
        ("(171)", "-171"),
        ("$ (0.02)", "-0.02"),
        ("—", "0"),
        ("$ 2.31 (1)", "2.31"),
        ("0.75*", "0.75"),
        ("−3", "-3"),
    ],
)
def test_numbers(cell: str, number: str) -> None:
    assert read_number(cell) == Decimal(number)


@pytest.mark.parametrize("cell", ["", "n/m", "NaN", "Infinity", "12 %"])
def test_not_numbers(cell: str) -> None:
    assert read_number(cell) is None


@pytest.mark.parametrize(
    ("value", "prior", "fails"),
    [
        (Decimal(110), Decimal(10), False),  # +1000% is still allowed
        (Decimal(111), Decimal(10), True),
        (Decimal(-111), Decimal(-10), True),  # a loss eleven times as large
        (Decimal(1990), Decimal(-134), False),  # the sign changed: no percentage (§9.9)
        (Decimal(5), None, False),
        (Decimal(5), Decimal(0), False),
    ],
)
def test_change_over_1000_percent(value: Decimal, prior: Decimal | None, fails: bool) -> None:
    assert (change_problem(value, prior) is not None) is fails


def test_revenue_must_be_above_zero() -> None:
    assert revenue_problem(Decimal(1)) is None
    assert revenue_problem(Decimal(0)) is not None


def test_eps_times_shares_is_near_net_income() -> None:
    shares = [Decimal(14_714_676_000)]

    assert eps_problem(Decimal("2.02"), shares, Decimal(29_789) * M) is None
    assert eps_problem(Decimal("2.20"), shares, Decimal(29_789) * M) is not None


def test_small_eps_is_allowed_its_rounding() -> None:
    # $0.05 × 100 million = $5.0 million against $5.4 million: 8% off, within half a cent.
    assert eps_problem(Decimal("0.05"), [Decimal(100_000_000)], Decimal(5_400_000)) is None


def test_net_income_to_common_is_the_basis_when_printed() -> None:
    shares = [Decimal(2_750_000_000)]
    net, to_common = Decimal(15_305) * M, Decimal(14_967) * M  # after preferred dividends

    assert eps_problem(Decimal("5.44"), shares, net, to_common) is None


def test_either_reading_of_the_share_count_may_agree() -> None:
    # "(in thousands, except per share data)" over a count printed whole.
    readings = [Decimal(396_589_354_000), Decimal(396_589_354)]

    assert eps_problem(Decimal("0.01"), readings, Decimal(2_521_000)) is None


def test_no_share_count_means_no_eps_check() -> None:
    assert eps_problem(Decimal("9.99"), [], Decimal(1)) is None
