"""Form 13F, a manager's quarterly report of its holdings (PLAN.md §9.8, E9-S2).

Pure: takes the bytes of a filing's two XML documents and returns what they say.

- The cover (`primary_doc.xml`, an `edgarSubmission`) gives the quarter, the report type, an
  amendment's number and type (RESTATEMENT or NEW HOLDINGS), and the summary page's line count
  and value total.
- The information table (a file the filer names, e.g. `56757.xml`; the index page gives its
  type, INFORMATION TABLE) lists the holdings: issuer, class, CUSIP, value, shares or
  principal, put or call, investment discretion. Lines are numbered from 1 in document order.
  One position can take several lines, one per other manager included (Berkshire Hathaway
  splits Ally Financial across four); merging is normalize/holdings.py's.
- Values are as filed: whole dollars in filings made on or after 2023-01-03, thousands before
  (normalize/holdings.py turns them into dollars).
- Elements are matched by local name: filers give the information table's namespace with a
  prefix or without one.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from marketdata.normalize.security_master import SecFileError

PARSER_VERSION = "1"  # the extractor_version of every stored line; raise it when output changes
METHOD = "f13_xml"

SUBMISSION_TYPES = frozenset({"13F-HR", "13F-HR/A"})
AMENDMENT_TYPES = frozenset({"RESTATEMENT", "NEW HOLDINGS"})
SHARE_TYPES = frozenset({"SH", "PRN"})
PUT_CALL = frozenset({"PUT", "CALL"})

_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_US_DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")  # the cover's MM-DD-YYYY
_TRUE = frozenset({"true", "1", "y"})
_FALSE = frozenset({"false", "0", "n"})


@dataclass(frozen=True)
class Cover:
    """The cover and summary pages of a 13F-HR or 13F-HR/A."""

    submission_type: str  # 13F-HR or 13F-HR/A
    schema_version: str | None
    report_period: date  # the quarter end (reportCalendarOrQuarter)
    filer_cik: int | None
    manager_name: str | None
    report_type: str | None  # 13F HOLDINGS REPORT, 13F COMBINATION REPORT
    is_amendment: bool
    amendment_no: int | None
    amendment_type: str | None  # RESTATEMENT or NEW HOLDINGS
    conf_denied_expired: bool | None  # NEW HOLDINGS: confidential treatment denied or expired
    date_reported: date | None  # an amendment's: when the holdings were first reported
    file_number: str | None  # the manager's 028- number
    other_managers: int | None  # included in this report
    entry_total: int | None  # the summary page's line count
    value_total: Decimal | None  # the summary page's total, as filed


@dataclass(frozen=True)
class Holding:
    """One line of the information table, as filed."""

    line_no: int
    name_of_issuer: str | None
    title_of_class: str | None
    cusip: str  # upper case, spaces removed
    figi: str | None
    value: Decimal  # as filed: dollars, or thousands before 2023-01-03
    shares: Decimal  # shares, or principal amount for PRN
    share_type: str  # SH or PRN
    put_call: str | None  # PUT or CALL
    investment_discretion: str | None  # SOLE, DFND or OTR
    other_managers: str | None  # the other managers' numbers, as filed: "2,4,11"


def parse_cover(data: bytes, where: str = "13F cover") -> Cover:
    """The cover of a 13F-HR or 13F-HR/A; SecFileError when the document isn't one."""
    root = _root(data, where)
    if _local(root) != "edgarSubmission":
        raise SecFileError(f"{where}: expected an edgarSubmission, found {_local(root)!r}")
    submission_type = _text(root, "headerData/submissionType")
    if submission_type not in SUBMISSION_TYPES:
        raise SecFileError(f"{where}: submission type {submission_type!r} is not a 13F-HR")
    cover = "formData/coverPage"
    period = _us_date(root, f"{cover}/reportCalendarOrQuarter", where) or _us_date(
        root, "headerData/filerInfo/periodOfReport", where
    )
    if period is None:
        raise SecFileError(f"{where}: no report period")
    amendment = f"{cover}/amendmentInfo"
    amendment_type = _text(root, f"{amendment}/amendmentType")
    if amendment_type is not None:
        amendment_type = amendment_type.upper()
        if amendment_type not in AMENDMENT_TYPES:
            raise SecFileError(f"{where}: amendment type {amendment_type!r} is not known")
    summary = "formData/summaryPage"
    return Cover(
        submission_type=submission_type,
        schema_version=_text(root, "schemaVersion"),
        report_period=period,
        filer_cik=_int(root, "headerData/filerInfo/filer/credentials/cik", where),
        manager_name=_text(root, f"{cover}/filingManager/name"),
        report_type=_upper(_text(root, f"{cover}/reportType")),
        is_amendment=_flag(root, f"{cover}/isAmendment", where) is True,
        amendment_no=_int(root, f"{cover}/amendmentNo", where),
        amendment_type=amendment_type,
        conf_denied_expired=_flag(root, f"{amendment}/confDeniedExpired", where),
        date_reported=_us_date(root, f"{amendment}/dateReported", where),
        file_number=_text(root, f"{cover}/form13FFileNumber"),
        other_managers=_int(root, f"{summary}/otherIncludedManagersCount", where),
        entry_total=_int(root, f"{summary}/tableEntryTotal", where),
        value_total=_number(root, f"{summary}/tableValueTotal", where),
    )


def parse_information_table(data: bytes, where: str = "13F information table") -> list[Holding]:
    """The lines of an information table, in document order."""
    root = _root(data, where)
    if _local(root) != "informationTable":
        raise SecFileError(f"{where}: expected an informationTable, found {_local(root)!r}")
    return [
        _holding(element, number, f"{where} line {number}")
        for number, element in enumerate(_children(root, "infoTable"), start=1)
    ]


def _holding(element: etree._Element, line_no: int, where: str) -> Holding:
    cusip = _text(element, "cusip")
    if cusip is None:
        raise SecFileError(f"{where}: no CUSIP")
    value = _number(element, "value", where)
    shares = _number(element, "shrsOrPrnAmt/sshPrnamt", where)
    if value is None or shares is None:
        raise SecFileError(f"{where}: no value or no number of shares")
    share_type = _upper(_text(element, "shrsOrPrnAmt/sshPrnamtType"))
    if share_type not in SHARE_TYPES:
        raise SecFileError(f"{where}: sshPrnamtType {share_type!r} is neither SH nor PRN")
    put_call = _upper(_text(element, "putCall"))
    if put_call is not None and put_call not in PUT_CALL:
        raise SecFileError(f"{where}: putCall {put_call!r} is neither Put nor Call")
    return Holding(
        line_no=line_no,
        name_of_issuer=_text(element, "nameOfIssuer"),
        title_of_class=_text(element, "titleOfClass"),
        cusip=cusip.replace(" ", "").upper(),
        figi=_text(element, "figi"),
        value=value,
        shares=shares,
        share_type=share_type,
        put_call=put_call,
        investment_discretion=_upper(_text(element, "investmentDiscretion")),
        other_managers=_text(element, "otherManager"),
    )


def _root(data: bytes, where: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        return etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SecFileError(f"{where}: not XML: {exc}") from None


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname


def _children(element: etree._Element, name: str) -> list[etree._Element]:
    """The child elements with this local name, whatever their namespace."""
    return [child for child in element if isinstance(child.tag, str) and _local(child) == name]


def _find(element: etree._Element, path: str) -> etree._Element | None:
    found: list[etree._Element] = [element]
    for name in path.split("/"):
        found = [child for parent in found for child in _children(parent, name)]
        if not found:
            return None
    return found[0]


def _text(element: etree._Element, path: str) -> str | None:
    found = _find(element, path)
    if found is None or found.text is None:
        return None
    return " ".join(found.text.split()) or None


def _upper(text: str | None) -> str | None:
    return text.upper() if text is not None else None


def _number(element: etree._Element, path: str, where: str) -> Decimal | None:
    text = _text(element, path)
    if text is None:
        return None
    if not _NUMBER.match(text):
        raise SecFileError(f"{where}: {path} is not a number: {text!r}")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise SecFileError(f"{where}: {path} is not a number: {text!r}") from None


def _int(element: etree._Element, path: str, where: str) -> int | None:
    number = _number(element, path, where)
    if number is None:
        return None
    if number != number.to_integral_value():
        raise SecFileError(f"{where}: {path} is not a whole number: {number}")
    return int(number)


def _us_date(element: etree._Element, path: str, where: str) -> date | None:
    text = _text(element, path)
    if text is None:
        return None
    match = _US_DATE.match(text)
    try:
        if match is None:
            raise ValueError(text)
        month, day, year = (int(part) for part in match.groups())
        return date(year, month, day)
    except ValueError:
        raise SecFileError(f"{where}: {path} is not a date (MM-DD-YYYY): {text!r}") from None


def _flag(element: etree._Element, path: str, where: str) -> bool | None:
    text = _text(element, path)
    if text is None:
        return None
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    raise SecFileError(f"{where}: {path} is not a boolean: {text!r}")
