"""E4a-S1: companyfacts JSON gives one fact per unit entry of its us-gaap and dei concepts, with
values as Decimal; anything not in that shape is counted, never guessed."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from marketdata.normalize.companyfacts import parse_companyfacts
from marketdata.normalize.security_master import SecFileError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts"
AAPL = (FIXTURES / "AAPL.json").read_bytes()
ACCN = "0000320193-26-000013"


def _file(facts: dict[str, Any], cik: int = 320193) -> bytes:
    return json.dumps({"cik": cik, "entityName": "Apple Inc.", "facts": facts}).encode()


def _fact(**fields: Any) -> dict[str, Any]:
    row = {
        "start": "2025-12-28",
        "end": "2026-03-28",
        "val": 95359000000,
        "accn": ACCN,
        "fy": 2026,
        "fp": "Q2",
        "form": "10-Q",
        "filed": "2026-05-01",
    }
    row.update(fields)
    return {key: value for key, value in row.items() if value is not None}


def _count(data: bytes) -> int:
    """Facts counted straight from the JSON: every entry in a unit list of us-gaap or dei."""
    document = json.loads(data)
    return sum(
        len(rows)
        for taxonomy in ("us-gaap", "dei")
        for concept in document["facts"].get(taxonomy, {}).values()
        for rows in concept["units"].values()
    )


def test_the_fixture_gives_one_fact_per_unit_entry() -> None:
    parsed = parse_companyfacts(AAPL)

    assert len(parsed.facts) == _count(AAPL) == 5895
    assert (parsed.skipped, parsed.repeated, parsed.conflicts) == (0, 0, ())
    assert parsed.cik == 320193 and parsed.entity_name == "Apple Inc."


def test_values_are_decimal_and_instants_have_no_start() -> None:
    facts = parse_companyfacts(AAPL).facts
    eps = [
        fact
        for fact in facts
        if fact.concept == "EarningsPerShareDiluted"
        and fact.period_end == date(2026, 6, 27)
        and fact.period_start == date(2026, 3, 29)
    ]
    assets = [fact for fact in facts if fact.concept == "Assets" and fact.period_start is None]

    assert [fact.value for fact in eps] == [Decimal("2.02")]  # the 10-Q of 2026-07-31
    assert all(isinstance(fact.value, Decimal) for fact in facts)
    assert assets and all(fact.unit == "USD" for fact in assets)


def test_an_empty_units_object_lists_no_facts() -> None:
    # The SEC answers KO's and V's NetIncomeLoss with "USD": {} (EDGE_CASES.md).
    data = _file({"us-gaap": {"NetIncomeLoss": {"label": "x", "units": {"USD": {}}}}})

    parsed = parse_companyfacts(data)

    assert (parsed.facts, parsed.skipped) == ((), 0)


def test_other_taxonomies_are_counted_not_loaded() -> None:
    data = _file(
        {
            "dei": {"EntityPublicFloat": {"units": {"USD": [_fact(start=None, end="2025-03-28")]}}},
            "srt": {"Revenues": {"units": {"USD": [_fact(), _fact(end="2026-06-27")]}}},
        }
    )

    parsed = parse_companyfacts(data)

    assert [(fact.taxonomy, fact.concept) for fact in parsed.facts] == [
        ("dei", "EntityPublicFloat")
    ]
    assert parsed.other_taxonomies == {"srt": 2}


@pytest.mark.parametrize(
    "fields",
    [
        {"val": "95359000000"},  # a string, not a number
        {"val": True},
        {"accn": "0000320193-26-13"},
        {"end": "2026-02-30"},
        {"start": "2026-04-01"},  # after the end
        {"filed": None},
    ],
)
def test_a_fact_out_of_shape_is_skipped_and_counted(fields: dict[str, Any]) -> None:
    data = _file({"us-gaap": {"Revenues": {"units": {"USD": [_fact(**fields), _fact()]}}}})

    parsed = parse_companyfacts(data)

    assert (len(parsed.facts), parsed.skipped) == (1, 1)


def test_a_repeated_key_keeps_the_first_and_reports_another_value() -> None:
    rows = [_fact(), _fact(), _fact(val=1)]
    parsed = parse_companyfacts(_file({"us-gaap": {"Revenues": {"units": {"USD": rows}}}}))

    assert [fact.value for fact in parsed.facts] == [Decimal(95359000000)]
    assert parsed.repeated == 1
    assert parsed.conflicts == (
        f"us-gaap:Revenues USD 2025-12-28 to 2026-03-28 in {ACCN}: 95359000000 kept, 1 dropped",
    )


def test_a_float_value_is_read_exactly() -> None:
    data = b'{"cik": 1, "facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares":'
    data += b'[{"start": "2025-12-28", "end": "2026-03-28", "val": 1.65, "accn": "'
    data += ACCN.encode() + b'", "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-05-01"}'
    data += b"]}}}}}"

    [fact] = parse_companyfacts(data).facts

    assert fact.value == Decimal("1.65") and fact.unit == "USD/shares" and fact.frame is None


@pytest.mark.parametrize("cik", [320193, "0000320193", "320193"])
def test_the_cik_is_an_integer_or_a_string_of_digits(cik: int | str) -> None:
    # SpaceX's file says "0001181412", ExxonMobil Holdings' "2115436" (EDGE_CASES.md).
    data = json.dumps({"cik": cik, "facts": {}}).encode()

    assert parse_companyfacts(data).cik == 320193


@pytest.mark.parametrize(
    "data",
    [
        b"not json",
        b"[]",
        b'{"cik": "AAPL", "facts": {}}',
        b'{"cik": 0, "facts": {}}',
        b'{"cik": 320193, "facts": []}',
        b'{"cik": 320193, "facts": {"us-gaap": []}}',
        b'{"cik": 320193, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"val": NaN}]}}}}}',
    ],
)
def test_a_file_out_of_shape_is_refused(data: bytes) -> None:
    with pytest.raises(SecFileError):
        parse_companyfacts(data)
