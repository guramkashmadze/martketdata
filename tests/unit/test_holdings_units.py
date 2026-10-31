"""E9-S2: 13F values are read in thousands before 2023-01-03 and in dollars from then, unless an
override pins a filer's unit; the cross-manager check flags an implied price more than 50% from
the median of at least three managers (D-023)."""

from datetime import date
from decimal import Decimal

import pytest

from marketdata.normalize.holdings import (
    PricePoint,
    UnitOverride,
    UnitOverrideError,
    in_dollars,
    parse_unit_overrides,
    unit_suspects,
    value_unit,
)

BAUPOST = 1061768
SOURCE = (
    "https://www.sec.gov/Archives/edgar/data/1061768/000106176826000010/"
    "0001061768-26-000010-index.htm"
)


def test_the_filing_date_decides_the_unit() -> None:
    assert value_unit(BAUPOST, date(2023, 1, 2)).unit == "thousands"
    assert value_unit(BAUPOST, date(2023, 1, 3)).unit == "dollars"
    assert value_unit(BAUPOST, date(2026, 8, 13)).rule == (
        "filed on or after 2023-01-03: whole dollars (SEC FAQ 62)"
    )
    assert in_dollars(Decimal("577212"), "thousands") == Decimal("577212000")
    assert in_dollars(Decimal("577211815"), "dollars") == Decimal("577211815")


def test_an_override_pins_a_filers_unit_for_its_dates() -> None:
    override = UnitOverride(BAUPOST, date(2023, 2, 1), None, "thousands", "evidence", SOURCE)
    closed = UnitOverride(1536411, date(2023, 11, 1), date(2024, 2, 14), "thousands", "e", SOURCE)

    assert value_unit(BAUPOST, date(2026, 8, 13), [override, closed]).unit == "thousands"
    assert value_unit(BAUPOST, date(2026, 8, 13), [override]).rule == "override: evidence"
    assert value_unit(BAUPOST, date(2023, 1, 31), [override]).unit == "dollars"
    assert value_unit(1067983, date(2026, 8, 14), [override]).unit == "dollars"  # another filer
    assert value_unit(1536411, date(2024, 2, 14), [closed]).unit == "thousands"
    assert value_unit(1536411, date(2024, 2, 15), [closed]).unit == "dollars"


def test_the_overrides_file_gives_dates_a_unit_a_reason_and_a_source() -> None:
    text = f"""
[[unit]]
manager_cik = {BAUPOST}
filed_from = 2023-02-14
unit = "thousands"
reason = "Q2 2026: 23 lines totalling 5,415,853"
source_url = "{SOURCE}"
"""
    [override] = parse_unit_overrides(text)

    assert override == UnitOverride(
        BAUPOST,
        date(2023, 2, 14),
        None,
        "thousands",
        "Q2 2026: 23 lines totalling 5,415,853",
        SOURCE,
    )
    assert parse_unit_overrides("") == ()


def test_override_problems_are_reported() -> None:
    entry = 'unit = "thousands"\nreason = "r"\nsource_url = "{0}"\n'.format(SOURCE)
    text = (
        f"[[unit]]\nmanager_cik = 1\nfiled_from = 2023-02-14\n{entry}"
        f"[[unit]]\nmanager_cik = 1\nfiled_from = 2024-01-01\nfiled_to = 2024-12-31\n{entry}"
        f"[[unit]]\nmanager_cik = 0\nfiled_from = 2023-02-14\n{entry}"
        f'[[unit]]\nmanager_cik = 2\nfiled_from = "2023-02-14"\n{entry}'
        f"[[unit]]\nmanager_cik = 2\nfiled_from = 2024-01-02\nfiled_to = 2024-01-01\n{entry}"
        '[[unit]]\nmanager_cik = 3\nfiled_from = 2024-01-01\nunit = "millions"\n'
        f'reason = "r"\nsource_url = "{SOURCE}"\n'
        '[[unit]]\nmanager_cik = 4\nfiled_from = 2024-01-01\nunit = "thousands"\n'
        'reason = " "\nsource_url = "http://example.com"\nnote = "x"\n'
    )

    with pytest.raises(UnitOverrideError) as raised:
        parse_unit_overrides(text, "f13_overrides.toml")

    assert str(raised.value).splitlines() == [
        "f13_overrides.toml:",
        "  - unit 2: CIK 1's dates overlap unit 1's",
        "  - unit 3: manager_cik must be a CIK (a positive whole number), got 0",
        "  - unit 4: filed_from and filed_to must be dates (YYYY-MM-DD, unquoted)",
        "  - unit 5: filed_to 2024-01-01 is before filed_from 2024-01-02",
        "  - unit 6: unit must be one of dollars, thousands, got 'millions'",
        "  - unit 7: unknown keys ['note']",
    ]


def _point(cik: int, value: str, shares: str = "100", **kind: str | None) -> PricePoint:
    return PricePoint(
        manager_cik=cik,
        cusip=str(kind.get("cusip", "037833100")),
        put_call=kind.get("put_call"),
        share_type=str(kind.get("share_type", "SH")),
        value_usd=Decimal(value),
        shares=Decimal(shares),
    )


def test_a_value_in_thousands_among_three_holders_is_unit_suspect() -> None:
    apple = ("037833100", None, "SH")
    points = [_point(1, "25500"), _point(2, "25400"), _point(3, "25.5")]  # 3 filed thousands

    assert unit_suspects(points) == {(3, apple)}


def test_two_holders_are_not_checked_and_each_way_of_holding_is_apart() -> None:
    assert unit_suspects([_point(1, "25500"), _point(2, "25.5")]) == set()
    points = [
        _point(1, "25500"),
        _point(2, "25400"),
        _point(3, "25600"),
        # Options are valued on their underlying and compared only with other calls.
        _point(4, "100", put_call="CALL"),
        _point(5, "25500", put_call="CALL"),
        _point(6, "25500", put_call="CALL"),
        _point(7, "0", shares="0"),  # no shares: no implied price
    ]

    assert unit_suspects(points) == {(4, ("037833100", "CALL", "SH"))}


def test_the_band_is_half_the_median_either_way() -> None:
    # The median of 48, 100, 100 and 149 a share is 100: 50 to 150 passes.
    points = [_point(1, "10000"), _point(2, "10000"), _point(3, "14900"), _point(4, "4800")]

    assert unit_suspects(points) == {(4, ("037833100", None, "SH"))}
