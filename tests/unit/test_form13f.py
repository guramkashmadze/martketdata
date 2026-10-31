"""E9-S2: 13F covers and information tables are read as filed, from real filings: a recent report
in dollars, an older one in thousands, a RESTATEMENT, a NEW HOLDINGS amendment, and a filer
that kept reporting thousands (PLAN.md §9.8)."""

import gzip
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.form13f import Holding, parse_cover, parse_information_table
from marketdata.normalize.security_master import SecFileError

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"


def _read(path: str) -> bytes:
    data = (FILINGS / path).read_bytes()
    return gzip.decompress(data) if path.endswith(".gz") else data


def test_a_recent_report_in_dollars() -> None:
    cover = parse_cover(_read("1067983/0001193125-26-352200/primary_doc.xml"))
    lines = parse_information_table(_read("1067983/0001193125-26-352200/56757.xml.gz"))

    assert (cover.submission_type, cover.report_period, cover.filer_cik) == (
        "13F-HR",
        date(2026, 6, 30),
        1067983,
    )
    assert (cover.schema_version, cover.report_type, cover.is_amendment) == (
        "X0202",
        "13F HOLDINGS REPORT",
        False,
    )
    assert (cover.other_managers, cover.entry_total, cover.value_total) == (
        14,
        89,
        Decimal("299253556246"),
    )
    assert len(lines) == 89 and sum(line.value for line in lines) == cover.value_total
    assert lines[0] == Holding(
        line_no=1,
        name_of_issuer="ALLY FINL INC",
        title_of_class="COM",
        cusip="02005N100",
        figi=None,
        value=Decimal("577211815"),
        shares=Decimal("12561737"),
        share_type="SH",
        put_call=None,
        investment_discretion="DFND",
        other_managers="4",
    )
    # One position across lines, one per other manager included: merged in E9-S4.
    assert [line.cusip for line in lines[:4]] == ["02005N100"] * 4


def test_an_older_report_in_thousands() -> None:
    cover = parse_cover(_read("1067983/0000950123-22-012275/primary_doc.xml"))
    lines = parse_information_table(_read("1067983/0000950123-22-012275/18337.xml.gz"))

    assert (cover.report_period, cover.report_type, cover.schema_version) == (
        date(2022, 9, 30),
        "13F COMBINATION REPORT",
        None,
    )
    assert (cover.entry_total, cover.value_total) == (179, Decimal("296096640"))
    assert len(lines) == 179 and sum(line.value for line in lines) == cover.value_total
    first = lines[0]
    assert (first.name_of_issuer, first.cusip, first.shares, first.value) == (
        "ACTIVISION BLIZZARD INC",
        "00507V109",
        Decimal("25645116"),
        Decimal("1906458"),  # thousands: 1,906,458,000 dollars, $74.34 a share
    )
    assert first.other_managers == "4,8,11"


def test_a_restatement_resubmits_the_whole_report() -> None:
    original = parse_information_table(_read("1709323/0002043585-26-000011/13fhciq425.xml"))
    cover = parse_cover(_read("1709323/0002043585-26-000012/primary_doc.xml"))
    restated = parse_information_table(_read("1709323/0002043585-26-000012/13fhciq425v2.xml"))

    assert (cover.submission_type, cover.is_amendment, cover.amendment_no) == ("13F-HR/A", True, 1)
    assert (cover.amendment_type, cover.date_reported) == ("RESTATEMENT", None)
    assert cover.report_period == date(2025, 12, 31) and len(restated) == len(original) == 9
    # Himalaya had Alphabet's two classes the wrong way round.
    shares = {line.cusip: line.shares for line in restated}
    assert {line.cusip: line.shares for line in original}["02079K305"] == Decimal("2451300")
    assert shares["02079K305"] == Decimal("2543300")
    assert shares["02079K107"] == Decimal("2451300")


def test_a_new_holdings_amendment_lists_the_holdings_whose_confidential_treatment_ended() -> None:
    cover = parse_cover(_read("1067983/0000950123-25-008361/primary_doc.xml"))
    lines = parse_information_table(_read("1067983/0000950123-25-008361/43981.xml"))

    assert (cover.amendment_type, cover.conf_denied_expired, cover.date_reported) == (
        "NEW HOLDINGS",
        True,
        date(2025, 5, 15),
    )
    assert [(line.name_of_issuer, line.cusip, line.shares) for line in lines] == [
        ("D R HORTON INC", "23331A109", Decimal("1512371")),
        ("LENNAR CORP", "526057104", Decimal("1929972")),
        ("LENNAR CORP", "526057302", Decimal("202")),
        ("NUCOR CORP", "670346105", Decimal("5756510")),
    ]


def test_a_filer_in_thousands_after_the_switch_reads_as_filed() -> None:
    # Baupost's Q2 2026: 23 positions totalling 5,415,853; data/f13_overrides.toml pins the unit.
    cover = parse_cover(_read("1061768/0001061768-26-000010/primary_doc.xml"))
    lines = parse_information_table(_read("1061768/0001061768-26-000010/BGLLCQ22026.xml"))

    assert (cover.entry_total, cover.value_total) == (23, Decimal("5415853"))
    alphabet = lines[0]
    assert (alphabet.cusip, alphabet.shares, alphabet.value) == (
        "02079K107",
        Decimal("1371931"),
        Decimal("484744"),
    )


PREFIXED = b"""<?xml version="1.0" encoding="UTF-8"?>
<ns1:informationTable xmlns:ns1="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <ns1:infoTable>
    <ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer>
    <ns1:titleOfClass>COM</ns1:titleOfClass>
    <ns1:cusip>037833 10 0</ns1:cusip>
    <ns1:value>255000</ns1:value>
    <ns1:shrsOrPrnAmt><ns1:sshPrnamt>1000</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType>
    </ns1:shrsOrPrnAmt>
    <ns1:putCall>Call</ns1:putCall>
    <ns1:investmentDiscretion>SOLE</ns1:investmentDiscretion>
  </ns1:infoTable>
</ns1:informationTable>"""


def test_elements_are_matched_by_local_name_and_codes_read_in_upper_case() -> None:
    [line] = parse_information_table(PREFIXED)

    assert (line.cusip, line.put_call, line.investment_discretion) == ("037833100", "CALL", "SOLE")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<oops", "not XML"),
        (b"<ownershipDocument/>", "expected an informationTable"),
        (PREFIXED.replace(b"<ns1:cusip>037833 10 0</ns1:cusip>", b""), "no CUSIP"),
        (PREFIXED.replace(b">Call<", b">Straddle<"), "neither Put nor Call"),
        (PREFIXED.replace(b">SH<", b">UNITS<"), "neither SH nor PRN"),
        (PREFIXED.replace(b"255000", b"2,550"), "not a number"),
    ],
)
def test_a_broken_table_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(SecFileError, match=message):
        parse_information_table(data)


def test_a_cover_that_isnt_a_13f_report_is_refused() -> None:
    cover = _read("1067983/0001193125-26-352200/primary_doc.xml")

    with pytest.raises(SecFileError, match="is not a 13F-HR"):
        parse_cover(cover.replace(b">13F-HR<", b">13F-NT<"))
    with pytest.raises(SecFileError, match="not a date"):
        parse_cover(cover.replace(b">06-30-2026<", b">2026-06-30<"))
