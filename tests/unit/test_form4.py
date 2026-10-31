"""E8-S1: Form 4 XML parsed into owners, trades and footnotes (PLAN.md §9.7), on real filings:
a 10b5-1 sale with derivative lines, joint filers, a 4/A, a filing from before the 10b5-1 box,
a foreign private issuer's converted price."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.form4 import (
    Form4,
    Line,
    parse_form4,
    xml_document_name,
    xml_names_in_index,
)
from marketdata.normalize.security_master import SecFileError

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"


def _filing(cik: int, accession: str, name: str) -> Form4:
    return parse_form4((FILINGS / str(cik) / accession / name).read_bytes(), accession)


def _apple() -> Form4:  # Ben Borders, Apple's principal accounting officer, October 2026
    return _filing(320193, "0001140361-26-040127", "form4.xml")


@pytest.mark.parametrize(
    ("primary_doc", "xml"),
    [
        ("xslF345X06/form4.xml", "form4.xml"),
        ("xslF345X03/form4.xml", "form4.xml"),
        ("xslF345X05/wk-form4a_1791925767.xml", "wk-form4a_1791925767.xml"),
        ("ownership.xml", "ownership.xml"),
    ],
)
def test_the_xml_is_the_primary_document_without_its_rendering_folder(
    primary_doc: str, xml: str
) -> None:
    assert xml_document_name(primary_doc) == xml


def test_a_folder_index_lists_its_xml_files_in_order() -> None:
    names = ["0001140361-26-040127.txt", "form4.xml", "ex24.htm", "Second.XML"]
    index = {"directory": {"item": [{"name": name} for name in names]}}

    assert xml_names_in_index(json.dumps(index).encode(), "x") == ["form4.xml", "Second.XML"]


@pytest.mark.parametrize("data", [b"{}", b"not json", b'{"directory": {"item": "x"}}'])
def test_an_index_without_a_listing_is_refused(data: bytes) -> None:
    with pytest.raises(SecFileError, match="no directory listing"):
        xml_names_in_index(data, "0001140361-26-040127")


def test_a_filing_gives_its_issuer_owner_and_10b5_1_box() -> None:
    filing = _apple()

    assert (filing.document_type, filing.schema_version) == ("4", "X0609")
    assert (filing.issuer_cik, filing.issuer_name, filing.issuer_symbol) == (
        320193,
        "Apple Inc.",
        "AAPL",
    )
    assert filing.period_of_report == date(2026, 10, 15)
    assert filing.original_filed_at is None
    assert filing.rule_10b5_1 is True  # written <aff10b5One>true</aff10b5One>
    (owner,) = filing.owners
    assert (owner.cik, owner.name, owner.officer_title) == (
        2100523,
        "Borders Ben",
        "Principal Accounting Officer",
    )
    assert (owner.is_director, owner.is_officer, owner.is_ten_pct_owner, owner.is_other) == (
        False,
        True,
        False,
        False,
    )


def test_lines_are_table_one_then_table_two_numbered_in_order() -> None:
    lines = _apple().lines

    assert [(line.line_no, line.is_derivative, line.code) for line in lines] == [
        (1, False, "M"),
        (2, False, "F"),
        (3, False, "S"),
        (4, False, "S"),
        (5, False, "S"),
        (6, False, "S"),
        (7, False, "S"),
        (8, True, "M"),
        (9, True, "M"),
        (10, True, "M"),
        (11, True, "M"),
    ]


def test_a_sale_keeps_its_values_and_the_footnotes_on_each_field() -> None:
    filing = _apple()
    sale = filing.lines[2]

    assert sale == Line(
        line_no=3,
        is_derivative=False,
        security_title="Common Stock",
        trade_date=date(2026, 10, 16),
        code="S",
        acquired_disposed="D",
        shares=Decimal("151"),
        price=Decimal("343.08"),
        owned_after=Decimal("39417"),
        direct_indirect="D",
        nature_of_ownership=None,
        timeliness=None,
        footnote_ids={"price": ("F5",), "security_title": ("F4",)},
    )
    assert filing.footnotes["F4"] == (
        "This transaction was made pursuant to a Rule 10b5-1 trading plan adopted by the "
        "reporting person on February 6, 2026."
    )
    assert filing.footnotes["F5"].startswith(
        "This transaction was executed in multiple trades at prices ranging from $342.69 to "
        "$343.49; the price reported above reflects the weighted average sale price."
    )


def test_the_10b5_1_box_is_the_filings_not_each_lines() -> None:
    """Apple's box is checked, yet only the sales carry the plan's footnote: the RSU settlement
    (M) and the shares withheld for tax (F) weren't made under the plan."""
    filing = _apple()
    plan = {
        line.line_no
        for line in filing.lines
        if any(
            "10b5-1" in filing.footnotes[note]
            for notes in line.footnote_ids.values()
            for note in notes
        )
    }

    assert filing.rule_10b5_1 is True
    assert plan == {3, 4, 5, 6, 7}


def test_a_line_without_a_price_keeps_the_footnote_that_explains_it() -> None:
    settlement = _apple().lines[0]

    assert (settlement.code, settlement.shares, settlement.price) == ("M", Decimal("1714"), None)
    assert settlement.footnote_ids == {"owned_after": ("F2",), "price": ("F1",)}


def test_a_derivative_line_gives_its_terms_and_underlying() -> None:
    rsu = _apple().lines[10]

    assert (rsu.security_title, rsu.code, rsu.acquired_disposed, rsu.shares) == (
        "Restricted Stock Unit",
        "M",
        "D",
        Decimal("330"),
    )
    assert (rsu.exercise_price, rsu.exercise_date, rsu.expiration_date) == (None, None, None)
    assert (rsu.underlying_title, rsu.underlying_shares, rsu.owned_after) == (
        "Common Stock",
        Decimal("330"),
        Decimal("1982"),
    )
    assert rsu.footnote_ids == {
        "exercise_date": ("F11",),
        "exercise_price": ("F1",),
        "expiration_date": ("F11",),
        "price": ("F1",),
    }


def test_joint_filers_share_the_filings_lines() -> None:
    """South32 and its subsidiary file one Form 4 about Trilogy Metals: one sale, two owners."""
    filing = _filing(1543418, "0001193125-26-378380", "ownership.xml")

    assert [(owner.cik, owner.name, owner.is_ten_pct_owner) for owner in filing.owners] == [
        (1635752, "South32 Ltd", True),
        (1726854, "South32 International Investment Holdings Proprietary Ltd", True),
    ]
    (sale,) = filing.lines
    assert (sale.code, sale.shares, sale.price, sale.direct_indirect) == (
        "S",
        Decimal("8215570"),
        Decimal("2.17"),
        "I",
    )
    assert sale.nature_of_ownership == "By wholly-owned subsidiary"
    assert filing.rule_10b5_1 is False  # written <aff10b5One>false</aff10b5One>
    assert filing.not_subject_to_section16 is True


def test_an_amendment_names_the_date_of_the_filing_it_amends() -> None:
    """Magnetar's four entities amend their Form 4 of the same day about CoreWeave: 24 sales
    by its funds, the share counts corrected."""
    filing = _filing(1769628, "0001104659-26-098473", "tm2623554-1_4aseq1.xml")

    assert (filing.document_type, filing.period_of_report, filing.original_filed_at) == (
        "4/A",
        date(2026, 8, 14),
        date(2026, 8, 14),
    )
    assert [owner.cik for owner in filing.owners] == [1352851, 1353085, 1368026, 1953511]
    assert len(filing.lines) == 24
    assert {line.code for line in filing.lines} == {"S"}
    assert filing.remarks is not None
    assert filing.remarks.startswith(
        "On August 14, 2026, the reporting persons filed a Form 4 which incorrectly stated"
    )


def test_an_amendment_can_have_no_lines() -> None:
    """Alphabet's 4/A for Frances Arnold only attaches a power of attorney (EDGE_CASES.md)."""
    filing = _filing(1652044, "0000950170-25-112321", "ownership.xml")

    assert (filing.document_type, filing.original_filed_at, filing.lines) == (
        "4/A",
        date(2025, 8, 29),
        (),
    )
    assert filing.remarks is not None and "Power of Attorney" in filing.remarks


def test_a_filing_from_before_the_10b5_1_box_has_none() -> None:
    """Marc Benioff's exercise and sales of February 2022 (schema X0306)."""
    filing = _filing(1108524, "0001127602-22-007431", "form4.xml")

    assert (filing.schema_version, filing.rule_10b5_1) == ("X0306", None)
    assert [(line.code, line.shares, line.price) for line in filing.lines[:3]] == [
        ("M", Decimal("2300"), Decimal("80.99")),
        ("S", Decimal("698"), Decimal("207.9927")),
        ("S", Decimal("682"), Decimal("208.9121")),
    ]
    assert filing.lines[-1].is_derivative


def test_a_box_written_as_one_is_checked() -> None:
    """Nebius director Arne Grimme's purchases: <aff10b5One>1</aff10b5One>."""
    filing = _filing(1513845, "0001513845-26-000136", "form4.xml")

    assert filing.rule_10b5_1 is True
    assert [(line.trade_date, line.code, line.price) for line in filing.lines] == [
        (date(2026, 10, 20), "P", Decimal("164.62")),
        (date(2026, 10, 21), "P", Decimal("167.94")),
    ]


def test_a_foreign_issuers_price_is_converted_to_dollars_with_the_rate_in_a_footnote() -> None:
    """TSMC's insiders buy in Taipei; the filer translates the price into US dollars."""
    filing = _filing(1046179, "0001046179-26-000723", "wk-form4_1791455750.xml")
    (purchase,) = filing.lines

    assert (purchase.security_title, purchase.code, purchase.price) == (
        "Common Shares (2330.TW)",
        "P",
        Decimal("79.39"),
    )
    assert filing.footnotes[purchase.footnote_ids["price"][0]] == (
        "The price was translated from the average purchase price of NT$2,524.2277 in New "
        "Taiwan dollars, at the rate of NT$31.795 to US$1."
    )


def _document(
    body: str, *, document_type: str = "4", box: str = "<aff10b5One>0</aff10b5One>"
) -> bytes:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0609</schemaVersion>
  <documentType>{document_type}</documentType>
  <periodOfReport>2026-10-15</periodOfReport>
  <issuer><issuerCik>0000320193</issuerCik><issuerName>Apple Inc.</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000000001</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  {box}
  {body}
</ownershipDocument>""".encode()


def _sale(*, shares: str = "100", price: str = "250.5", day: str = "2026-10-15") -> str:
    return f"""<nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>{day}</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType>
        <transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>900</value>
      </sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>"""


_HOLDING = """<nonDerivativeHolding>
      <securityTitle><value>Common Stock</value></securityTitle>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value>
      </sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
      <natureOfOwnership><value>By Trust</value></natureOfOwnership></ownershipNature>
    </nonDerivativeHolding>"""


def test_holdings_are_not_trades_and_take_no_line_number() -> None:
    rows = _HOLDING + _sale() + _HOLDING + _sale(shares="50")
    table = f"<nonDerivativeTable>{rows}</nonDerivativeTable>"

    filing = parse_form4(_document(table))

    assert [(line.line_no, line.shares) for line in filing.lines] == [
        (1, Decimal("100")),
        (2, Decimal("50")),
    ]


def test_a_date_with_a_time_zone_is_read_as_its_day() -> None:
    table = f"<nonDerivativeTable>{_sale(day='2026-10-15-05:00')}</nonDerivativeTable>"

    (line,) = parse_form4(_document(table)).lines

    assert line.trade_date == date(2026, 10, 15)


def test_a_relationship_box_left_out_is_unchecked() -> None:
    (owner,) = parse_form4(_document("")).owners

    assert (owner.is_director, owner.is_officer, owner.is_ten_pct_owner) == (True, False, False)


def test_a_form_without_the_box_has_none() -> None:
    assert parse_form4(_document("", box="")).rule_10b5_1 is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<ownershipDocument><documentType>4", "not XML"),
        (b'<?xml version="1.0"?><edgarSubmission/>', "expected an ownershipDocument"),
        (_document("", document_type="5"), "is not a Form 4 or 4/A"),
        (_document("", box="<aff10b5One>yes</aff10b5One>"), "is not a boolean"),
        (
            _document(f"<nonDerivativeTable>{_sale(price='$250')}</nonDerivativeTable>"),
            "is not a number",
        ),
        (
            _document(f"<nonDerivativeTable>{_sale(day='15/10/2026')}</nonDerivativeTable>"),
            "is not a date",
        ),
        (
            _document(
                "<nonDerivativeTable>"
                + _sale().replace("<transactionCode>S</transactionCode>", "")
                + "</nonDerivativeTable>"
            ),
            "no transaction code",
        ),
    ],
)
def test_a_document_that_isnt_a_form_4_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(SecFileError, match=message):
        parse_form4(data)


def test_entities_are_not_expanded() -> None:
    """A document type declaration can't pull in files or expand entities."""
    data = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        + _document("").split(b"?>", 1)[1].replace(b"<remarks/>", b"")
    ).replace(b"<issuerName>Apple Inc.</issuerName>", b"<issuerName>&x;</issuerName>")

    filing = parse_form4(data)

    assert filing.issuer_name is None
