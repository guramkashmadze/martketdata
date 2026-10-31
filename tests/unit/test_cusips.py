"""E9-S3: a CUSIP's check digit is checked before it is sent; OpenFIGI's answer for it is a
match (one FIGI), unmatched or ambiguous (D-121)."""

import json

import pytest

from marketdata.normalize.cusips import (
    CusipMatch,
    MappingError,
    decide,
    mapping_jobs,
    parse_mapping,
    valid_cusip,
)

APPLE = {
    "figi": "BBG000B9XRY4",
    "name": "APPLE INC",
    "ticker": "AAPL",
    "exchCode": "US",
    "compositeFIGI": "BBG000B9XRY4",
    "securityType": "Common Stock",
    "marketSector": "Equity",
}


@pytest.mark.parametrize(
    ("cusip", "valid"),
    [
        ("037833100", True),  # Apple
        ("084670702", True),  # Berkshire Hathaway class B
        ("H1467J104", True),  # Chubb, a Swiss issuer
        ("02079K305", True),  # Alphabet class A
        ("037833101", False),  # a wrong check digit
        ("03783310", False),  # eight characters
        ("03783310A", False),  # a letter for the check digit
        ("037833-00", False),
    ],
)
def test_the_check_digit_is_checked(cusip: str, valid: bool) -> None:
    assert valid_cusip(cusip) is valid


def test_each_cusip_is_asked_for_on_its_us_listing() -> None:
    assert mapping_jobs(["037833100"]) == [
        {"idType": "ID_CUSIP", "idValue": "037833100", "exchCode": "US"}
    ]


def test_one_figi_is_a_match() -> None:
    match = decide("037833100", {"data": [APPLE, dict(APPLE)]})  # the same FIGI twice

    assert match == CusipMatch(
        "037833100",
        "matched",
        figi="BBG000B9XRY4",
        ticker="AAPL",
        name="APPLE INC",
        exch_code="US",
        security_type="Common Stock",
        market_sector="Equity",
        candidates=1,
    )


def test_no_figi_or_an_error_is_unmatched_and_two_are_ambiguous() -> None:
    assert decide("650111107", {"warning": "No identifier found."}) == CusipMatch(
        "650111107", "unmatched", note="OpenFIGI: No identifier found."
    )
    assert decide("650111107", {"error": "Invalid idValue format."}).note == (
        "OpenFIGI: Invalid idValue format."
    )
    other = {**APPLE, "figi": "BBG000XXXXXX", "ticker": "AAPL2"}
    ambiguous = decide("037833100", {"data": [APPLE, other]})
    assert (ambiguous.status, ambiguous.figi, ambiguous.candidates) == ("ambiguous", None, 2)
    assert ambiguous.note == "OpenFIGI gave 2 FIGIs: AAPL, AAPL2"


def test_a_response_gives_one_result_per_job() -> None:
    data = json.dumps([{"data": [APPLE]}, {"warning": "No identifier found."}]).encode()

    assert [decide(c, r).status for c, r in zip(["a", "b"], parse_mapping(data, 2))] == [
        "matched",
        "unmatched",
    ]
    with pytest.raises(MappingError, match="1 results for 2 jobs"):
        parse_mapping(json.dumps([{}]).encode(), 2)
    with pytest.raises(MappingError, match="not JSON"):
        parse_mapping(b"<html>", 1)
