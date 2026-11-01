"""E15: numbers on pages are never rounded (PLAN.md §9.14): proof pages show every digit with the
unit; tables show each value exactly in their stated scale."""

from decimal import Decimal

import pytest

from marketdata.web.render import amount, change, direction, exact, scale_name, scaled


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("391035000000"), "391,035,000,000"),
        (Decimal("1234.50"), "1,234.5"),
        (Decimal("-0.0700"), "-0.07"),
        (Decimal("0.00"), "0"),
        (7, "7"),
        (None, "—"),
    ],
)
def test_exact_keeps_every_digit(value: Decimal | int | None, text: str) -> None:
    assert exact(value) == text


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (Decimal("391035000000"), "USD", "391,035"),
        (Decimal("12345678000"), "USD", "12,345.678"),  # a filer reporting in thousands
        (Decimal("-2500000"), "USD", "-2.5"),
        (Decimal("14840390000"), "shares", "14,840.39"),
        (Decimal("1.57"), "USD/shares", "1.57"),
        (Decimal("46.5"), "percent", "46.5%"),
        (None, "USD", "—"),
    ],
)
def test_scaled_shows_the_exact_value_in_the_tables_scale(
    value: Decimal | None, unit: str, text: str
) -> None:
    assert scaled(value, unit) == text


def test_scale_names() -> None:
    assert [scale_name(unit) for unit in ("USD", "shares", "USD/shares", "percent")] == [
        "USD millions",
        "millions of shares",
        "USD per share",
        "%",
    ]


def test_amounts_keep_every_digit_and_their_unit() -> None:
    assert amount(Decimal("109417000000"), "USD") == "109,417,000,000 USD"
    assert amount(Decimal("2.02"), "USD/shares") == "2.02 USD per share"
    assert amount(Decimal("16.4"), "percent") == "16.4%"
    assert amount(None, "USD") == "—"


def test_changes_carry_their_sign_and_a_direction_never_a_verdict() -> None:
    assert [change(Decimal(v)) for v in ("5.3", "-2.1", "0")] == ["+5.3%", "-2.1%", "0%"]
    assert change(None) == "—"
    assert [direction(v) for v in (Decimal("5.3"), Decimal("-2.1"), Decimal(0), None)] == [
        "up",
        "down",
        "",
        "",
    ]
