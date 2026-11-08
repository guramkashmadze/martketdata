"""E16-S1: the structured Schedule 13D and 13G XML parsed into filers, issuers, classes and
reporting persons (PLAN.md §9.15), on real filings: both schema versions of each form, an
amendment that exits at 0%, joint filings, one below 5%, and files without a namespace."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.schedule13 import (
    Schedule13,
    parse_schedule13,
    schedule_of,
    xml_document_name,
)
from marketdata.normalize.security_master import SecFileError

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"


def _filing(cik: int, accession: str) -> Schedule13:
    return parse_schedule13(
        (FILINGS / str(cik) / accession / "primary_doc.xml").read_bytes(), accession
    )


def _xml(form: str = "SCHEDULE 13G", persons: str | None = None, **cover: str) -> bytes:
    """A small 13G in the second schema version, with what a test changes."""
    person = """
      <coverPageHeaderReportingPersonDetails>
        <reportingPersonName>Holder LP</reportingPersonName>
        <reportingPersonBeneficiallyOwnedNumberOfShares>
          <soleVotingPower>10</soleVotingPower><soleDispositivePower>10</soleDispositivePower>
        </reportingPersonBeneficiallyOwnedNumberOfShares>
        <reportingPersonBeneficiallyOwnedAggregateNumberOfShares>10</reportingPersonBeneficiallyOwnedAggregateNumberOfShares>
        <classPercent>6.1</classPercent>
        <typeOfReportingPerson>PN</typeOfReportingPerson>
      </coverPageHeaderReportingPersonDetails>"""
    fields = {"event": "03/31/2026", "cik": "0000002488", **cover}
    return f"""<?xml version="1.0"?>
    <edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g">
      <schemaVersion>X0202</schemaVersion>
      <headerData><submissionType>{form}</submissionType>
        <filerInfo><filer><filerCredentials><cik>0000000042</cik></filerCredentials></filer></filerInfo>
      </headerData>
      <formData>
        <coverPageHeader>
          <securitiesClassTitle>Common Stock</securitiesClassTitle>
          <eventDateRequiresFilingThisStatement>{fields["event"]}</eventDateRequiresFilingThisStatement>
          <issuerInfo><issuerCik>{fields["cik"]}</issuerCik><issuerName>X</issuerName></issuerInfo>
        </coverPageHeader>
        {person if persons is None else persons}
      </formData>
    </edgarSubmission>""".encode()


@pytest.mark.parametrize(
    ("primary_doc", "xml"),
    [
        ("xslSCHEDULE_13G_X02/primary_doc.xml", "primary_doc.xml"),
        ("xslSCHEDULE_13D_X01/primary_doc.xml", "primary_doc.xml"),
        ("primary_doc.xml", "primary_doc.xml"),
    ],
)
def test_the_xml_is_the_primary_document_without_its_rendering_folder(
    primary_doc: str, xml: str
) -> None:
    assert xml_document_name(primary_doc) == xml


@pytest.mark.parametrize(
    ("form", "schedule"),
    [
        ("SCHEDULE 13D", "13D"),
        ("SCHEDULE 13D/A", "13D"),
        ("SC 13D/A", "13D"),
        ("SCHEDULE 13G", "13G"),
        ("SC 13G/A", "13G"),
    ],
)
def test_every_form_code_belongs_to_its_family(form: str, schedule: str) -> None:
    assert schedule_of(form) == schedule


def test_a_13g_gives_its_filer_issuer_class_rule_and_holder() -> None:
    # Vanguard Capital Management on AMD, the second schema version
    filing = _filing(2488, "0002100119-26-000052")

    assert (filing.form, filing.schedule, filing.schema_version) == (
        "SCHEDULE 13G",
        "13G",
        "X0202",
    )
    assert (filing.filer_cik, filing.previous_accession, filing.amendment_no) == (
        2100119,
        None,
        None,
    )
    assert (filing.issuer_cik, filing.issuer_name, filing.cusips) == (
        2488,
        "Advanced Micro Devices Inc",
        ("007903107",),
    )
    assert (filing.class_title, filing.event_date) == ("Common Stock", date(2026, 3, 31))
    assert filing.rules == ("Rule 13d-1(b)",)
    assert filing.ceased_5pct is None  # item 5 marked not applicable
    assert filing.purpose is None
    (person,) = filing.persons
    assert (person.position, person.name, person.cik, person.types, person.citizenship) == (
        1,
        "Vanguard Capital Management",
        None,  # a 13G names no reporting person's CIK
        ("IA",),
        "PA",
    )
    assert (person.amount, person.percent) == (Decimal(122092206), Decimal("7.48"))
    assert (
        person.sole_voting,
        person.shared_voting,
        person.sole_dispositive,
        person.shared_dispositive,
    ) == (Decimal(16151940), Decimal(0), Decimal(122092206), Decimal(0))


def test_a_13g_amendment_that_exits_ticks_item_5() -> None:
    # The Vanguard Group's AMD stake moved to Vanguard Capital Management in March 2026
    filing = _filing(2488, "0000102909-26-000540")

    assert (filing.form, filing.filer_cik, filing.amendment_no) == ("SCHEDULE 13G/A", 102909, 13)
    assert filing.ceased_5pct is True
    assert filing.event_date == date(2026, 3, 13)
    (person,) = filing.persons
    assert (person.name, person.amount, person.percent) == (
        "The Vanguard Group",
        Decimal(0),
        Decimal(0),
    )


def test_the_first_schema_version_has_one_cusip_and_names_the_previous_filing() -> None:
    # Baillie Gifford on MercadoLibre, filed before the schema had a version
    filing = _filing(1099590, "0001088875-24-000108")

    assert (filing.schema_version, filing.amendment_no) == (None, 16)
    assert filing.previous_accession == "0001088875-11-000008"
    assert filing.cusips == ("58733R102",)
    (person,) = filing.persons
    assert (person.name, person.citizenship) == ("BAILLIE GIFFORD & CO", "X0")
    assert (person.amount, person.percent) == (Decimal("4689139.00"), Decimal("9.3"))


def test_a_joint_13g_keeps_every_reporting_person_in_order() -> None:
    # Eric Schmidt and his family's entities on Alphabet's Class A, under Rule 13d-1(d)
    filing = _filing(1652044, "0000950170-25-022699")

    assert filing.rules == ("Rule 13d-1(d)",)
    assert [person.position for person in filing.persons] == list(range(1, len(filing.persons) + 1))
    assert [person.name for person in filing.persons[:3]] == [
        "Eric E. Schmidt",
        "The Schmidt Family Living Trust",
        "The Schmidt Family Foundation",
    ]
    assert filing.persons[0].percent == Decimal("0.97")
    assert filing.persons[2].types == ()  # the foundation's type is left blank
    assert all(person.percent < 5 for person in filing.persons)


def test_a_13d_gives_each_persons_cik_and_its_item_4_word_for_word() -> None:
    # The Walton family's 13D on Walmart, the first schema version
    filing = _filing(104169, "0001140361-24-049960")

    assert (filing.form, filing.schedule, filing.schema_version) == ("SCHEDULE 13D", "13D", None)
    assert (filing.issuer_cik, filing.cusips, filing.event_date) == (
        104169,
        ("931142103",),
        date(2024, 12, 18),
    )
    assert filing.rules == ()
    assert filing.ceased_5pct is None
    enterprises, trust = filing.persons
    assert (enterprises.name, enterprises.cik, enterprises.member_of_group) == (
        "Walton Enterprises, LLC",
        1535243,
        "a",
    )
    assert (enterprises.amount, enterprises.percent) == (Decimal(3606663095), Decimal("44.9"))
    assert (trust.name, trust.cik) == ("Walton Family Holdings Trust", None)  # NoCIK Y
    assert filing.purpose is not None
    assert filing.purpose.startswith("Prior to December 18, 2024, Alice L. Walton, Jim C. Walton")
    assert len(filing.purpose) == 3369  # as filed, not cut short
    assert filing.purpose.count("\n") == 6  # its paragraph breaks are kept


def test_a_13d_amendment_of_the_second_version_lists_its_cusips() -> None:
    # Fresenius Medical Care's US holding company and its parent, below 5% of Humacyte
    filing = _filing(1818382, "0001104659-26-098152")

    assert (filing.form, filing.schema_version, filing.amendment_no) == (
        "SCHEDULE 13D/A",
        "X0202",
        11,
    )
    assert filing.previous_accession == "0001104659-21-112194"
    assert filing.cusips == ("020751103",)
    assert [(person.cik, person.percent) for person in filing.persons] == [
        (42872, Decimal("4.9")),
        (1333141, Decimal("4.9")),
    ]
    assert filing.persons[1].citizenship == "2M"  # EDGAR's code for Germany


@pytest.mark.parametrize(
    ("cik", "accession", "form", "issuer"),
    [
        (1822966, "0001124198-26-000045", "SCHEDULE 13D/A", 1822966),  # Fluor on NuScale
        (1513845, "0001045810-26-000062", "SCHEDULE 13G", 1513845),  # NVIDIA on Nebius
    ],
)
def test_a_file_without_a_namespace_reads_the_same(
    cik: int, accession: str, form: str, issuer: int
) -> None:
    filing = _filing(cik, accession)

    assert (filing.form, filing.issuer_cik, filing.schema_version) == (form, issuer, "X0202")
    assert filing.persons


def test_a_person_can_name_several_types() -> None:
    fluor, enterprises = _filing(1822966, "0001124198-26-000045").persons

    assert fluor.types == ("CO", "HC")
    assert enterprises.cik is None


def test_item_5_unticked_at_exactly_5_percent() -> None:
    # State Street on Nike: 5 as filed, while saying it still owns more than 5%
    filing = _filing(320187, "0000093751-26-000274")

    assert filing.ceased_5pct is False
    assert filing.persons[0].percent == Decimal(5)


def test_a_small_document_parses() -> None:
    filing = parse_schedule13(_xml())

    assert (filing.filer_cik, filing.issuer_cik, filing.event_date) == (42, 2488, date(2026, 3, 31))
    (person,) = filing.persons
    assert (person.amount, person.shared_voting) == (Decimal(10), None)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not xml", "not XML"),
        (b"<ownershipDocument/>", "expected an edgarSubmission"),
        (_xml(form="13F-HR"), "is not a structured 13D or 13G"),
        (_xml(form="SC 13G"), "is not a structured 13D or 13G"),
        (_xml(persons=""), "no reporting person"),
        (_xml(event="2026-13-45"), "is not a date"),
        (_xml(event="31 March 2026"), "is not a date"),
        (_xml(cik="ABC"), "is not a CIK"),
        (
            _xml(
                persons="<coverPageHeaderReportingPersonDetails><reportingPersonName>X"
                "</reportingPersonName><reportingPersonBeneficiallyOwnedAggregateNumberOfShares>"
                "1,000</reportingPersonBeneficiallyOwnedAggregateNumberOfShares><classPercent>5"
                "</classPercent></coverPageHeaderReportingPersonDetails>"
            ),
            "is not a number",
        ),
        (
            _xml(
                persons="<coverPageHeaderReportingPersonDetails><reportingPersonName>X"
                "</reportingPersonName><classPercent>5</classPercent>"
                "</coverPageHeaderReportingPersonDetails>"
            ),
            "no reportingPersonBeneficiallyOwnedAggregateNumberOfShares",
        ),
    ],
)
def test_a_document_that_isnt_a_structured_schedule_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(SecFileError, match=message):
        parse_schedule13(data, "0000000042-26-000001")
