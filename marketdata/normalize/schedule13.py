"""Schedules 13D and 13G, the structured XML a holder of more than 5% files (PLAN.md §9.15,
E16-S1).

Pure: takes the document's bytes and returns its filer, issuer, class, event date and reporting
persons.

- The primary document in submissions is EDGAR's rendering of the XML, e.g.
  `xslSCHEDULE_13G_X02/primary_doc.xml`; the XML itself has the same name in the filing folder
  (`xml_document_name`, as for Form 4).
- Each form comes in two schema versions: the first carries no `schemaVersion` and a single
  `issuerCusip`, the second (`X0202`, from March 2026) lists `issuerCusips`. The 13G's namespace
  is `http://www.sec.gov/edgar/schedule13g`, the 13D's `…/schedule13D`, and a few files have
  none, so elements are matched by local name. The two forms name the same things apart
  (`issuerCik` and `issuerCIK`, `classPercent` and `percentOfClass`).
- Dates are written MM/DD/YYYY. Amounts and powers are decimals (`13603235.00`), percents as
  filed (`7.48`, `5`, `0.0`).
- Reporting persons: a 13D gives each one's CIK, or `reportingPersonNoCIK` Y; a 13G gives none,
  only the filer's CIK in its header.
- A 13G names the rule it is filed under (13d-1(b), (c) or (d)) and, in item 5, whether the
  holder no longer owns more than 5% of the class. A 13D's Item 4, the purpose of the
  transaction, is kept word for word, quoted, never summarized (D-014).
- Addresses, phone numbers, signatures and Item 2's statements about persons are not kept.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from lxml import etree

from marketdata.normalize.filings import STAKE_FORMS
from marketdata.normalize.form4 import xml_document_name
from marketdata.normalize.security_master import SecFileError

__all__ = [
    "DOCUMENT_TYPES",
    "METHOD",
    "PARSER_VERSION",
    "Person",
    "Schedule13",
    "parse_schedule13",
    "schedule_of",
    "xml_document_name",
]

PARSER_VERSION = "1"  # the extractor_version of every stored row; raise it when output changes
METHOD = "schedule13_xml"

DOCUMENT_TYPES = STAKE_FORMS  # SCHEDULE 13D, 13D/A, 13G and 13G/A (D-061)

type Schedule = Literal["13D", "13G"]

_DATE_US = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_YES = frozenset({"y", "yes", "true", "1"})
_NO = frozenset({"n", "no", "false", "0"})


@dataclass(frozen=True)
class Person:
    """A reporting person and what its cover page states (rows 1 to 14 of a 13D, 1 to 12 of a
    13G)."""

    position: int  # its order in the filing, from 1
    name: str
    cik: int | None  # a 13D's; None for a 13G, or a 13D person without a CIK
    types: tuple[str, ...]  # IA, HC, IN, CO, PN, BD, FI, BK, IV, OO ... as filed
    citizenship: str | None  # citizenship or place of organization, as filed (EDGAR codes)
    amount: Decimal  # aggregate amount beneficially owned, shares of the class
    percent: Decimal  # percent of class, as filed
    sole_voting: Decimal | None
    shared_voting: Decimal | None
    sole_dispositive: Decimal | None
    shared_dispositive: Decimal | None
    member_of_group: str | None  # the group box: a (a member of a group) or b (not)


@dataclass(frozen=True)
class Schedule13:
    form: str  # SCHEDULE 13D, SCHEDULE 13D/A, SCHEDULE 13G or SCHEDULE 13G/A
    schema_version: str | None  # X0202; None for the first version, which states none
    filer_cik: int  # the filer the header names (its EDGAR credentials)
    previous_accession: str | None  # the filing an amendment names as the one before it
    amendment_no: int | None
    issuer_cik: int
    issuer_name: str | None
    cusips: tuple[str, ...]  # as filed
    class_title: str | None
    event_date: date  # the date of the event that requires the filing
    rules: tuple[str, ...]  # a 13G's: Rule 13d-1(b), (c) or (d), as filed
    ceased_5pct: bool | None  # a 13G's item 5: no longer owns more than 5% of the class
    purpose: str | None  # a 13D's Item 4, word for word
    persons: tuple[Person, ...]

    @property
    def schedule(self) -> Schedule:
        return schedule_of(self.form)


def schedule_of(form: str) -> Schedule:
    """13D or 13G, for any form code of either family (old or structured)."""
    return "13D" if "13D" in form.upper() else "13G"


def parse_schedule13(data: bytes, where: str = "Schedule 13D or 13G") -> Schedule13:
    """The filing in a structured Schedule 13D or 13G document (or an amendment);
    SecFileError when it isn't one."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SecFileError(f"{where}: not XML: {exc}") from None
    if _local(root) != "edgarSubmission":
        raise SecFileError(f"{where}: expected an edgarSubmission, found {_local(root)!r}")
    form = _text(root, "headerData", "submissionType")
    if form not in DOCUMENT_TYPES:
        raise SecFileError(f"{where}: submission type {form!r} is not a structured 13D or 13G")
    head = _required(root, where, "formData", "coverPageHeader")
    issuer = _required(head, where, "issuerInfo")
    is_13d = schedule_of(form) == "13D"
    event = (
        _text(head, "dateOfEvent")
        if is_13d
        else _text(head, "eventDateRequiresFilingThisStatement")
    )
    if event is None:
        raise SecFileError(f"{where}: no date of the event that requires the filing")
    persons = (
        tuple(
            _person_13d(element, position, where)
            for position, element in enumerate(
                _all(root, "formData", "reportingPersons", "reportingPersonInfo"), start=1
            )
        )
        if is_13d
        else tuple(
            _person_13g(element, position, where)
            for position, element in enumerate(
                _all(root, "formData", "coverPageHeaderReportingPersonDetails"), start=1
            )
        )
    )
    if not persons:
        raise SecFileError(f"{where}: no reporting person")
    return Schedule13(
        form=form,
        schema_version=_text(root, "schemaVersion"),
        filer_cik=_cik(
            _text(root, "headerData", "filerInfo", "filer", "filerCredentials", "cik"),
            "the filer's CIK",
            where,
        ),
        previous_accession=_accession(_text(root, "headerData", "previousAccessionNumber")),
        amendment_no=_whole(_text(head, "amendmentNo"), "amendmentNo", where),
        issuer_cik=_cik(_text(issuer, "issuerCIK" if is_13d else "issuerCik"), "issuer CIK", where),
        issuer_name=_text(issuer, "issuerName"),
        cusips=_cusips(issuer),
        class_title=_text(head, "securitiesClassTitle"),
        event_date=_date(event, "the event date", where),
        rules=tuple(
            text
            for element in _all(
                head,
                "designateRulesPursuantThisScheduleFiled",
                "designateRulePursuantThisScheduleFiled",
            )
            if (text := _collapse(element.text or ""))
        ),
        ceased_5pct=(
            None
            if is_13d
            else _flag(
                _text(root, "formData", "items", "item5", "classOwnership5PercentOrLess"),
                "item 5",
                where,
            )
        ),
        purpose=(
            _verbatim(root, "formData", "items1To7", "item4", "transactionPurpose")
            if is_13d
            else None
        ),
        persons=persons,
    )


def _person_13d(element: etree._Element, position: int, where: str) -> Person:
    at = f"{where} reporting person {position}"
    no_cik = _flag(_text(element, "reportingPersonNoCIK"), "reportingPersonNoCIK", at)
    cik_text = _text(element, "reportingPersonCIK")
    cik = None
    if no_cik is not True and cik_text is not None and cik_text.strip("0"):
        cik = _cik(cik_text, "reportingPersonCIK", at)
    return Person(
        position=position,
        name=_name(element, at),
        cik=cik,
        types=_types(element),
        citizenship=_text(element, "citizenshipOrOrganization"),
        amount=_required_number(element, "aggregateAmountOwned", at),
        percent=_required_number(element, "percentOfClass", at),
        sole_voting=_number(element, "soleVotingPower", at),
        shared_voting=_number(element, "sharedVotingPower", at),
        sole_dispositive=_number(element, "soleDispositivePower", at),
        shared_dispositive=_number(element, "sharedDispositivePower", at),
        member_of_group=_group(_text(element, "memberOfGroup")),
    )


def _person_13g(element: etree._Element, position: int, where: str) -> Person:
    at = f"{where} reporting person {position}"
    powers = _first(element, "reportingPersonBeneficiallyOwnedNumberOfShares")
    return Person(
        position=position,
        name=_name(element, at),
        cik=None,
        types=_types(element),
        citizenship=_text(element, "citizenshipOrOrganization"),
        amount=_required_number(
            element, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares", at
        ),
        percent=_required_number(element, "classPercent", at),
        sole_voting=_number(powers, "soleVotingPower", at),
        shared_voting=_number(powers, "sharedVotingPower", at),
        sole_dispositive=_number(powers, "soleDispositivePower", at),
        shared_dispositive=_number(powers, "sharedDispositivePower", at),
        member_of_group=_group(_text(element, "memberGroup")),
    )


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname


def _children(element: etree._Element | None, name: str) -> list[etree._Element]:
    if element is None:
        return []
    return [child for child in element if isinstance(child.tag, str) and _local(child) == name]


def _all(element: etree._Element | None, *path: str) -> list[etree._Element]:
    """The elements at a path of local names, in document order."""
    found = [element] if element is not None else []
    for name in path:
        found = [child for parent in found for child in _children(parent, name)]
    return found


def _first(element: etree._Element | None, *path: str) -> etree._Element | None:
    found = _all(element, *path)
    return found[0] if found else None


def _required(element: etree._Element, where: str, *path: str) -> etree._Element:
    found = _first(element, *path)
    if found is None:
        raise SecFileError(f"{where}: no {'/'.join(path)}")
    return found


def _text(element: etree._Element | None, *path: str) -> str | None:
    found = _first(element, *path)
    if found is None or found.text is None:
        return None
    return _collapse(found.text) or None


def _verbatim(element: etree._Element, *path: str) -> str | None:
    """An element's text as filed, only stripped at its ends: a quote, never rewritten."""
    found = _first(element, *path)
    if found is None:
        return None
    return "".join(found.itertext()).strip() or None


def _name(element: etree._Element, where: str) -> str:
    name = _text(element, "reportingPersonName")
    if name is None:
        raise SecFileError(f"{where}: no name")
    return name


def _types(element: etree._Element) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            text.upper()
            for found in _children(element, "typeOfReportingPerson")
            if (text := _collapse(found.text or ""))
        )
    )


def _cusips(issuer: etree._Element) -> tuple[str, ...]:
    listed = [
        *_all(issuer, "issuerCusips", "issuerCusipNumber"),
        *_children(issuer, "issuerCusip"),
        *_children(issuer, "issuerCUSIP"),
    ]
    return tuple(dict.fromkeys(text for found in listed if (text := _collapse(found.text or ""))))


def _group(text: str | None) -> str | None:
    return text.lower() if text and text.lower() in ("a", "b") else None


def _number(element: etree._Element | None, name: str, where: str) -> Decimal | None:
    text = _text(element, name)
    if text is None:
        return None
    if not _NUMBER.match(text):
        raise SecFileError(f"{where}: {name} is not a number: {text!r}")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise SecFileError(f"{where}: {name} is not a number: {text!r}") from None


def _required_number(element: etree._Element, name: str, where: str) -> Decimal:
    found = _number(element, name, where)
    if found is None:
        raise SecFileError(f"{where}: no {name}")
    return found


def _whole(text: str | None, name: str, where: str) -> int | None:
    if text is None:
        return None
    if not text.isdigit():
        raise SecFileError(f"{where}: {name} is not a whole number: {text!r}")
    return int(text)


def _date(text: str, name: str, where: str) -> date:
    us, iso = _DATE_US.match(text), _DATE_ISO.match(text)
    try:
        if us:
            return date(int(us.group(3)), int(us.group(1)), int(us.group(2)))
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    except ValueError:
        pass
    raise SecFileError(f"{where}: {name} is not a date: {text!r}")


def _flag(text: str | None, name: str, where: str) -> bool | None:
    if text is None:
        return None
    if text.lower() in _YES:
        return True
    if text.lower() in _NO:
        return False
    raise SecFileError(f"{where}: {name} is not a yes or no: {text!r}")


def _cik(text: str | None, name: str, where: str) -> int:
    if text is None or not text.isdigit() or int(text) <= 0:
        raise SecFileError(f"{where}: {name} is not a CIK: {text!r}")
    return int(text)


def _accession(text: str | None) -> str | None:
    """An accession number as stored, with dashes; None when the filing names none or garbles
    it."""
    if text is None:
        return None
    digits = text.replace("-", "")
    if len(digits) != 18 or not digits.isdigit():
        return None
    formatted = f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"
    return formatted if _ACCESSION.match(formatted) else None


def _collapse(text: str) -> str:
    return " ".join(text.split())
