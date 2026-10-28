"""E4a-S5: universe growth by public float (D-035, D-085): frames of dei:EntityPublicFloat,
each filer's latest value, and the ranking for review with stale and missing values apart."""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from marketdata.normalize.public_float import (
    Filer,
    FramePoint,
    instant_frames,
    latest_points,
    parse_frame,
    rank,
)
from marketdata.normalize.security_master import SecFileError

TODAY = date(2026, 10, 28)


def _frame(rows: list[Any], ccp: str = "CY2025Q2I") -> bytes:
    document = {
        "taxonomy": "dei",
        "tag": "EntityPublicFloat",
        "ccp": ccp,
        "uom": "USD",
        "label": "Entity Public Float",
        "pts": len(rows),
        "data": rows,
    }
    return json.dumps(document).encode()


def _row(cik: Any, value: Any = 75322105637, end: str = "2025-08-01") -> dict[str, Any]:
    return {
        "accn": "0000027419-26-000016",
        "cik": cik,
        "entityName": "TARGET CORP",
        "loc": "US-MN",
        "end": end,
        "val": value,
    }


def test_a_frame_gives_one_point_per_filer() -> None:
    frame = parse_frame(_frame([_row(27419), _row("0000320193", 3.25e12), _row(True), "x"]))

    assert (frame.taxonomy, frame.concept, frame.unit, frame.frame) == (
        "dei",
        "EntityPublicFloat",
        "USD",
        "CY2025Q2I",
    )
    assert [(point.cik, point.value) for point in frame.points] == [
        (27419, Decimal(75322105637)),
        (320193, Decimal("3.25E+12")),
    ]
    assert frame.skipped == 2
    assert frame.points[0].period_start is None and frame.points[0].period_end == date(2025, 8, 1)


@pytest.mark.parametrize(
    "data", [b"[]", b"no", _frame([], ccp="2025Q2"), b'{"taxonomy": "dei", "data": []}']
)
def test_a_frame_out_of_shape_is_refused(data: bytes) -> None:
    with pytest.raises(SecFileError):
        parse_frame(data)


def test_nine_quarters_before_today_newest_first() -> None:
    assert instant_frames(TODAY) == [
        (2026, 3),
        (2026, 2),
        (2026, 1),
        (2025, 4),
        (2025, 3),
        (2025, 2),
        (2025, 1),
        (2024, 4),
        (2024, 3),
    ]
    assert instant_frames(date(2026, 2, 1), count=2) == [(2025, 4), (2025, 3)]


def _point(cik: int, value: int, end: date, accession: str = "0000000001-26-000001") -> FramePoint:
    return FramePoint(cik, f"FILER {cik}", None, end, Decimal(value), accession)


def test_each_filers_latest_value_counts() -> None:
    older = _point(1, 10, date(2024, 6, 28))
    newer = _point(1, 12, date(2025, 6, 30))

    assert latest_points([newer, older, _point(2, 5, date(2025, 3, 28))])[1] == newer


def _filer(cik: int, *tickers: str, kind: str = "unknown", universe: bool = False) -> Filer:
    return Filer(cik, f"Company {cik}", kind, universe, tickers)


def test_the_ranking_leaves_out_what_it_cant_rank_and_lists_the_rest_apart() -> None:
    recent, old = date(2025, 6, 30), date(2024, 6, 28)
    latest = {
        1: _point(1, 300, recent),  # in the universe
        2: _point(2, 500, recent),  # not checked
        3: _point(3, 900, recent),  # a commodity trust: a fund
        4: _point(4, 800, recent),  # no listed ticker
        5: _point(5, 700, old),  # stale: no 10-K for two years
        6: _point(6, 100, recent),
        34088: _point(34088, 400, recent),  # the predecessor's value
    }
    filers = {
        1: _filer(1, "AAA", kind="domestic", universe=True),
        2: _filer(2, "BBB"),
        3: _filer(3, "GLD", kind="fund", universe=True),
        4: _filer(4),
        5: _filer(5, "EEE"),
        6: _filer(6, "FFF"),
        7: _filer(7, "GGG", kind="domestic", universe=True),  # no value at all
        2115436: _filer(2115436, "XOM", kind="domestic", universe=True),
    }

    suggestion = rank(latest, filers, today=TODAY, top=3, predecessors={2115436: [34088]})

    assert [(c.rank, c.tickers, c.public_float) for c in suggestion.candidates] == [
        (1, ("BBB",), Decimal(500)),
        (2, ("XOM",), Decimal(400)),
        (3, ("AAA",), Decimal(300)),
    ]
    assert suggestion.candidates[0].filer_type == "not checked"
    assert suggestion.candidates[1].note == "value of predecessor CIK 34088"
    assert suggestion.candidates[2].in_universe
    assert [(row.status, row.cik) for row in suggestion.unranked] == [
        ("stale", 5),
        ("missing", 7),
    ]
    assert suggestion.left_out == 2  # the fund and the filer without a ticker


def test_a_float_implying_more_than_10000_dollars_a_share_is_suspect() -> None:
    # Cabot's 2025 cover page: 4,429,047,299,000,000 dollars for 53 million shares.
    recent = date(2025, 3, 31)
    latest = {
        1: _point(1, 4_429_047_299_000_000, recent, "0001193125-25-292412"),
        2: _point(2, 3_253_431_000_000, recent, "0000320193-25-000079"),
        3: _point(3, 0, recent, "0000000003-25-000001"),
        4: _point(4, 900, recent, "0000000004-25-000001"),  # no share count: not checked
        5: _point(5, 1_967_000_000_000, recent, "0000000005-25-000001"),  # 7,000 a share
    }
    filers = {cik: _filer(cik, f"T{cik}") for cik in latest}
    shares = {1: Decimal(53_100_000), 2: Decimal(14_776_353_000), 5: Decimal(281_000_000)}

    suggestion = rank(latest, filers, today=TODAY, top=10, shares=shares)

    assert [candidate.cik for candidate in suggestion.candidates] == [2, 5, 4]
    assert suggestion.candidates[1].note.startswith("7,000 dollars a share")
    assert suggestion.candidates[0].note == ""
    assert [(row.status, row.cik) for row in suggestion.unranked] == [
        ("suspect", 1),
        ("suspect", 3),
    ]
    assert suggestion.unranked[0].note.startswith("83,409,554 dollars a share")
    assert suggestion.unranked[1].note == "the cover page states a float of 0"
