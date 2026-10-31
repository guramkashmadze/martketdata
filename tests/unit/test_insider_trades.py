"""E8-S5: served insider trades grouped per insider group, day, code, security and table
(§9.7): shares summed, the price weighted by shares, the value exact; flags never change a
value."""

from datetime import date
from decimal import Decimal

from marketdata.normalize.form4 import Line
from marketdata.normalize.insiders import FiledTrade, group_trades

FILED = date(2026, 10, 19)
TRADED = date(2026, 10, 16)


def _line(
    line_no: int,
    shares: str | None,
    price: str | None,
    *,
    code: str = "S",
    direction: str = "D",
    title: str = "Common Stock",
    day: date = TRADED,
    owned: str = "1000",
    ownership: str = "D",
) -> Line:
    return Line(
        line_no=line_no,
        is_derivative=False,
        security_title=title,
        trade_date=day,
        code=code,
        acquired_disposed=direction,
        shares=Decimal(shares) if shares is not None else None,
        price=Decimal(price) if price is not None else None,
        owned_after=Decimal(owned),
        direct_indirect=ownership,
        nature_of_ownership=None if ownership == "D" else "By Trust",
        timeliness=None,
    )


def _trade(line: Line, *, owners: tuple[int, ...] = (1,), accession: str = "A-1") -> FiledTrade:
    return FiledTrade(accession, owners, True, FILED, line)


def test_lines_are_summed_with_the_price_weighted_by_shares() -> None:
    """Apple's officer, 2026-10-16: five sales of 151, 80, 969, 40 and 40 shares."""
    lines = [
        _line(3, "151", "343.08", owned="39417"),
        _line(4, "80", "344.18", owned="39337"),
        _line(5, "969", "345.74", owned="38368"),
        _line(6, "40", "346.22", owned="38328"),
        _line(7, "40", "347.43", owned="38288"),
    ]

    [group] = group_trades([_trade(line) for line in lines])

    assert (group.shares, group.average_price, group.value) == (
        Decimal("1280"),
        Decimal("345.3965"),
        Decimal("442107.54"),
    )
    assert (group.holdings_after, group.direct_indirect, group.rule_10b5_1) == (
        Decimal("38288"),
        "D",
        True,
    )
    assert group.checks == ("issuer_matches_listing", "direction_matches_code", "price_reported")
    assert group.flags == ()


def test_joint_filers_count_once_and_classes_and_days_stay_apart() -> None:
    trades = [
        _trade(_line(1, "100", "10"), owners=(7, 8)),  # a fund and its manager: one group
        _trade(_line(2, "50", "11"), owners=(7, 8)),
        _trade(_line(3, "10", "300", title="Class C Capital Stock"), owners=(7, 8)),
        _trade(_line(4, "5", "12", day=date(2026, 10, 15)), owners=(7, 8)),
        _trade(_line(1, "20", "10"), owners=(9,), accession="A-2"),
    ]

    groups = group_trades(trades)

    assert [(g.owner_ciks, g.trade_date.day, g.security_title, g.shares) for g in groups] == [
        ((7, 8), 16, "Common Stock", Decimal("150")),
        ((7, 8), 16, "Class C Capital Stock", Decimal("10")),
        ((9,), 16, "Common Stock", Decimal("20")),
        ((7, 8), 15, "Common Stock", Decimal("5")),
    ]


def test_holdings_are_given_only_when_the_lines_share_one_ownership() -> None:
    [group] = group_trades(
        [_trade(_line(1, "100", "10")), _trade(_line(2, "100", "10", ownership="I"))]
    )

    assert (group.holdings_after, group.direct_indirect) == (None, None)


def test_flags_say_what_is_missing_or_odd_without_changing_a_value() -> None:
    no_price = group_trades([_trade(_line(1, "15000", None))])[0]
    some_price = group_trades([_trade(_line(1, "10", "5")), _trade(_line(2, "10", None))])[0]
    acquiring_sale = group_trades([_trade(_line(1, "2500", "44.9", direction="A"))])[0]
    late = group_trades([_trade(_line(1, "683", "1", day=date(2027, 1, 25)))])[0]

    assert (no_price.flags, no_price.value, no_price.shares) == (
        ("price_not_reported",),
        None,
        Decimal("15000"),
    )
    assert (some_price.flags, some_price.value, some_price.average_price) == (
        ("price_partly_reported",),
        Decimal("50"),
        Decimal("5.0000"),
    )
    assert acquiring_sale.flags == ("direction_differs_from_code",)
    assert "direction_matches_code" not in acquiring_sale.checks
    assert late.flags == ("trade_date_after_filing",)


def test_a_gift_has_no_direction_check_and_its_missing_price_is_flagged() -> None:
    [gift] = group_trades([_trade(_line(1, "100", None, code="G"))])

    assert gift.checks == ("issuer_matches_listing",)
    assert gift.flags == ("price_not_reported",)
