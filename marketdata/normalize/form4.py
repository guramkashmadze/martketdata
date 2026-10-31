"""Form 4, the XML `ownershipDocument` an insider files (PLAN.md §9.7, E8-S1).

Pure: takes the document's bytes and returns its issuer, owners, trades and footnotes.

- A Form 4's primary document in submissions is EDGAR's rendering of the XML, e.g.
  `xslF345X06/form4.xml`; the XML itself has the same name in the filing folder
  (`xml_document_name`).
- Booleans come as `1`/`0` or `true`/`false`, both in the same year's filings.
- The 10b5-1 box (`aff10b5One`, from April 2023) is one per filing: at least one of its trades
  was made under a plan meant to meet Rule 10b5-1(c), and footnotes say which. Earlier forms
  have no box (None).
- A trade is a line of table I (non-derivative) or table II (derivative); lines are numbered
  from 1 in document order, table I first. Holdings (positions reported without a trade) are
  not trades and are skipped.
- Each line keeps the footnote ids attached to each of its fields; the texts are the filing's.
- Prices are as filed. Foreign private issuers' insiders, who report since March 2026, convert
  prices from yen, New Taiwan or Hong Kong dollars into US dollars and give the rate in a
  footnote (EDGE_CASES.md, E8-S1).
- Owners' addresses and signatures are not kept.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from marketdata.normalize.security_master import SecFileError

PARSER_VERSION = "1"  # the extractor_version of every stored line; raise it when output changes
METHOD = "form4_xml"

DOCUMENT_TYPES = frozenset({"4", "4/A"})

# The field each element's footnotes belong to; any other element keeps its own name.
FOOTNOTE_FIELDS = {
    "securityTitle": "security_title",
    "conversionOrExercisePrice": "exercise_price",
    "transactionDate": "trade_date",
    "deemedExecutionDate": "deemed_execution_date",
    "transactionCoding": "code",
    "transactionTimeliness": "timeliness",
    "transactionShares": "shares",
    "transactionPricePerShare": "price",
    "transactionAcquiredDisposedCode": "acquired_disposed",
    "exerciseDate": "exercise_date",
    "expirationDate": "expiration_date",
    "underlyingSecurityTitle": "underlying_title",
    "underlyingSecurityShares": "underlying_shares",
    "sharesOwnedFollowingTransaction": "owned_after",
    "directOrIndirectOwnership": "direct_indirect",
    "natureOfOwnership": "nature_of_ownership",
}

_XSL_FOLDER = re.compile(r"^xsl[^/]*/", re.IGNORECASE)
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")  # xs:date may carry a time zone after the day
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_TRUE = frozenset({"1", "true"})
_FALSE = frozenset({"0", "false"})


@dataclass(frozen=True)
class Owner:
    """A reporting owner and the boxes ticked for its relationship to the issuer."""

    cik: int
    name: str
    is_director: bool
    is_officer: bool
    officer_title: str | None
    is_ten_pct_owner: bool
    is_other: bool
    other_text: str | None


@dataclass(frozen=True)
class Line:
    """One trade as filed. Derivative lines also give the derivative's terms and underlying."""

    line_no: int
    is_derivative: bool
    security_title: str | None
    trade_date: date
    code: str  # P, S, A, M, F, G, D ... stored as filed (§9.7)
    acquired_disposed: str | None  # A or D
    shares: Decimal | None
    price: Decimal | None  # per share, as filed
    owned_after: Decimal | None
    direct_indirect: str | None  # D or I
    nature_of_ownership: str | None  # e.g. "By Trust"
    timeliness: str | None  # E (early) or L (late), when marked
    exercise_price: Decimal | None = None
    exercise_date: date | None = None
    expiration_date: date | None = None
    underlying_title: str | None = None
    underlying_shares: Decimal | None = None
    footnote_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # price -> F5


@dataclass(frozen=True)
class Form4:
    document_type: str  # 4 or 4/A
    schema_version: str | None
    period_of_report: date
    original_filed_at: date | None  # a 4/A's dateOfOriginalSubmission
    not_subject_to_section16: bool | None
    rule_10b5_1: bool | None  # the filing's 10b5-1 box; None before the form had one
    issuer_cik: int
    issuer_name: str | None
    issuer_symbol: str | None
    owners: tuple[Owner, ...]
    lines: tuple[Line, ...]
    footnotes: Mapping[str, str]  # id -> text, whitespace collapsed
    remarks: str | None


def xml_document_name(primary_doc: str) -> str:
    """The XML behind a Form 4's primary document: EDGAR lists its rendering
    (`xslF345X06/form4.xml`), whose XML is `form4.xml` in the filing folder (PLAN.md §8)."""
    return _XSL_FOLDER.sub("", primary_doc.strip())


def parse_form4(data: bytes, where: str = "Form 4") -> Form4:
    """The filing in a Form 4 or 4/A XML document; SecFileError when it isn't one."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SecFileError(f"{where}: not XML: {exc}") from None
    if root.tag != "ownershipDocument":
        raise SecFileError(f"{where}: expected an ownershipDocument, found {root.tag!r}")
    document_type = _text(root, "documentType")
    if document_type not in DOCUMENT_TYPES:
        raise SecFileError(f"{where}: document type {document_type!r} is not a Form 4 or 4/A")
    owners = tuple(_owner(element, where) for element in root.findall("reportingOwner"))
    if not owners:
        raise SecFileError(f"{where}: no reporting owner")
    lines: list[Line] = []
    for table, row, derivative in (
        ("nonDerivativeTable", "nonDerivativeTransaction", False),
        ("derivativeTable", "derivativeTransaction", True),
    ):
        for element in root.findall(f"{table}/{row}"):
            lines.append(_line(element, len(lines) + 1, derivative, where))
    remarks = root.find("remarks")
    return Form4(
        document_type=document_type,
        schema_version=_text(root, "schemaVersion"),
        period_of_report=_required_date(root, "periodOfReport", where),
        original_filed_at=_date(root, "dateOfOriginalSubmission", where),
        not_subject_to_section16=_flag(root, "notSubjectToSection16", where),
        rule_10b5_1=_flag(root, "aff10b5One", where),
        issuer_cik=_cik(root, "issuer/issuerCik", where),
        issuer_name=_text(root, "issuer/issuerName"),
        issuer_symbol=_text(root, "issuer/issuerTradingSymbol"),
        owners=owners,
        lines=tuple(lines),
        footnotes={
            note.get("id", ""): _collapse("".join(note.itertext()))
            for note in root.findall("footnotes/footnote")
        },
        remarks=_collapse("".join(remarks.itertext())) or None if remarks is not None else None,
    )


def _owner(element: etree._Element, where: str) -> Owner:
    relationship = "reportingOwnerRelationship"
    name = _text(element, "reportingOwnerId/rptOwnerName")
    if name is None:
        raise SecFileError(f"{where}: a reporting owner has no name")
    return Owner(
        cik=_cik(element, "reportingOwnerId/rptOwnerCik", where),
        name=name,
        is_director=_flag(element, f"{relationship}/isDirector", where) is True,
        is_officer=_flag(element, f"{relationship}/isOfficer", where) is True,
        officer_title=_text(element, f"{relationship}/officerTitle"),
        is_ten_pct_owner=_flag(element, f"{relationship}/isTenPercentOwner", where) is True,
        is_other=_flag(element, f"{relationship}/isOther", where) is True,
        other_text=_text(element, f"{relationship}/otherText"),
    )


def _line(element: etree._Element, line_no: int, derivative: bool, where: str) -> Line:
    at = f"{where} line {line_no}"
    code = _text(element, "transactionCoding/transactionCode")
    if code is None:
        raise SecFileError(f"{at}: no transaction code")
    amounts = "transactionAmounts"
    line = Line(
        line_no=line_no,
        is_derivative=derivative,
        security_title=_value(element, "securityTitle"),
        trade_date=_required_date(element, "transactionDate/value", at),
        code=code.upper(),
        acquired_disposed=_code(element, f"{amounts}/transactionAcquiredDisposedCode"),
        shares=_number(element, f"{amounts}/transactionShares", at),
        price=_number(element, f"{amounts}/transactionPricePerShare", at),
        owned_after=_number(element, "postTransactionAmounts/sharesOwnedFollowingTransaction", at),
        direct_indirect=_code(element, "ownershipNature/directOrIndirectOwnership"),
        nature_of_ownership=_value(element, "ownershipNature/natureOfOwnership"),
        timeliness=_code(element, "transactionTimeliness"),
        footnote_ids=_footnote_ids(element),
    )
    if not derivative:
        return line
    return replace(
        line,
        exercise_price=_number(element, "conversionOrExercisePrice", at),
        exercise_date=_date(element, "exerciseDate/value", at),
        expiration_date=_date(element, "expirationDate/value", at),
        underlying_title=_value(element, "underlyingSecurity/underlyingSecurityTitle"),
        underlying_shares=_number(element, "underlyingSecurity/underlyingSecurityShares", at),
    )


def _footnote_ids(element: etree._Element) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for note in element.iter("footnoteId"):
        parent = note.getparent()
        if parent is None or not note.get("id"):
            continue
        name = FOOTNOTE_FIELDS.get(str(parent.tag), str(parent.tag))
        ids = found.setdefault(name, [])
        if note.get("id") not in ids:
            ids.append(str(note.get("id")))
    return {name: tuple(ids) for name, ids in sorted(found.items())}


def _text(element: etree._Element, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    return _collapse(found.text) or None


def _value(element: etree._Element, path: str) -> str | None:
    """A field's `<value>`; fields that can carry footnotes wrap their value in one."""
    return _text(element, f"{path}/value")


def _code(element: etree._Element, path: str) -> str | None:
    value = _value(element, path)
    return value.upper() if value else None


def _number(element: etree._Element, path: str, where: str) -> Decimal | None:
    text = _value(element, path)
    if text is None:
        return None
    if not _NUMBER.match(text):
        raise SecFileError(f"{where}: {path} is not a number: {text!r}")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise SecFileError(f"{where}: {path} is not a number: {text!r}") from None


def _date(element: etree._Element, path: str, where: str) -> date | None:
    text = _text(element, path)
    if text is None:
        return None
    match = _DATE.match(text)
    try:
        return date.fromisoformat(match.group(1)) if match else _bad_date(path, text, where)
    except ValueError:
        return _bad_date(path, text, where)


def _required_date(element: etree._Element, path: str, where: str) -> date:
    found = _date(element, path, where)
    if found is None:
        raise SecFileError(f"{where}: no {path}")
    return found


def _bad_date(path: str, text: str, where: str) -> date:
    raise SecFileError(f"{where}: {path} is not a date: {text!r}")


def _flag(element: etree._Element, path: str, where: str) -> bool | None:
    text = _text(element, path)
    if text is None:
        return None
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    raise SecFileError(f"{where}: {path} is not a boolean: {text!r}")


def _cik(element: etree._Element, path: str, where: str) -> int:
    text = _text(element, path)
    if text is None or not text.isdigit() or int(text) <= 0:
        raise SecFileError(f"{where}: {path} is not a CIK: {text!r}")
    return int(text)


def _collapse(text: str) -> str:
    return " ".join(text.split())
