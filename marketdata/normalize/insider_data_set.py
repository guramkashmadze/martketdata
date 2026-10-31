"""The SEC's Insider Transactions Data Sets (PLAN.md §8, E8-S4): a ZIP per quarter of the Forms
3, 4 and 5 filed in it, flattened into tab-separated files. They check our parsing of each
filing's XML (`marketdata insiders reconcile`); what we serve comes from the XML itself.

Pure: takes the bytes of the landing page and of a quarter's ZIP.

- The landing page links each quarter's ZIP (`2026q3_form345.zip`, 8 to 13 MB in 2025-2026),
  under more than one folder (D-045, EDGE_CASES.md E1).
- SUBMISSION.tsv has one row per filing: dates as `31-JUL-2026`, the issuer's CIK zero-padded,
  the document type (3, 4, 4/A, 5 ...) and the 10b5-1 box as AFF10B5ONE, written 0/1,
  false/true or empty, like the XML's `aff10b5One`.
- NONDERIV_TRANS.tsv and DERIV_TRANS.tsv have one row per trade, numbers written with a decimal
  point (`4012678.0`), in no particular order: lines are compared as a multiset.
"""

import csv
import io
import re
import zipfile
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from marketdata.normalize.insiders import TradeLine
from marketdata.normalize.security_master import SecFileError

QUARTER = re.compile(r"^(\d{4})q([1-4])$")
FORMS = frozenset({"4", "4/A"})
_TRUE, _FALSE = frozenset({"1", "true"}), frozenset({"0", "false"})


def quarter_dates(quarter: str) -> tuple[date, date]:
    """The first and last day of a quarter written like `2026q3`."""
    match = QUARTER.match(quarter)
    if match is None:
        raise ValueError(f"a quarter is written like 2026q3, not {quarter!r}")
    year, number = int(match[1]), int(match[2])
    first = date(year, 3 * number - 2, 1)
    last = date(year + 1, 1, 1) if number == 4 else date(year, 3 * number + 1, 1)
    return first, date.fromordinal(last.toordinal() - 1)


def zip_name(quarter: str) -> str:
    quarter_dates(quarter)
    return f"{quarter}_form345.zip"


def data_set_link(page: bytes, quarter: str) -> str | None:
    """The path the landing page links a quarter's ZIP at, e.g.
    /files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip."""
    name = re.escape(zip_name(quarter))
    match = re.search(rf'href="(/[^"]*/{name})"', page.decode("utf-8", "replace"))
    return match[1] if match else None


@dataclass(frozen=True)
class DataSetLine:
    """A trade as the data set flattens it; what the reconciliation compares."""

    is_derivative: bool
    security_title: str | None
    trade_date: date
    code: str
    acquired_disposed: str | None
    shares: Decimal | None
    price: Decimal | None
    owned_after: Decimal | None
    direct_indirect: str | None


@dataclass(frozen=True)
class DataSetFiling:
    accession_no: str
    issuer_cik: int
    form: str
    filed_at: date
    rule_10b5_1: bool | None
    lines: tuple[DataSetLine, ...]


def read_data_set(data: bytes, issuers: Collection[int]) -> dict[str, DataSetFiling]:
    """The Forms 4 and 4/A about these issuers in a quarter's ZIP, by accession number."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SecFileError(f"the insider data set is not a ZIP: {exc}") from None
    wanted = set(issuers)
    filings: dict[str, tuple[int, str, date, bool | None]] = {}
    for row in _rows(archive, "SUBMISSION.tsv"):
        cik = int(row["ISSUERCIK"] or 0)
        if cik in wanted and row["DOCUMENT_TYPE"] in FORMS:
            filings[row["ACCESSION_NUMBER"]] = (
                cik,
                row["DOCUMENT_TYPE"],
                _date(row["FILING_DATE"]),
                _flag(row["AFF10B5ONE"]),
            )
    lines: dict[str, list[DataSetLine]] = {accession: [] for accession in filings}
    for name, derivative in (("NONDERIV_TRANS.tsv", False), ("DERIV_TRANS.tsv", True)):
        for row in _rows(archive, name):
            accession = row["ACCESSION_NUMBER"]
            if accession in lines:
                lines[accession].append(_line(row, derivative))
    return {
        accession: DataSetFiling(accession, cik, form, filed_at, box, tuple(lines[accession]))
        for accession, (cik, form, filed_at, box) in filings.items()
    }


type Status = Literal["same", "differs", "only_ours", "only_data_set"]


@dataclass(frozen=True)
class OurFiling:
    """A filing as we stored it from its XML, every line as filed (superseded or not)."""

    accession_no: str
    issuer_cik: int
    form: str
    filed_at: date
    rule_10b5_1: bool | None
    lines: Sequence[TradeLine]


@dataclass(frozen=True)
class FilingCheck:
    accession_no: str
    issuer_cik: int
    status: Status
    lines: int  # the trades compared: ours, or the data set's when we have none
    differences: tuple[str, ...]


def reconcile(
    ours: Mapping[str, OurFiling], theirs: Mapping[str, DataSetFiling]
) -> list[FilingCheck]:
    """Each filing of either side, compared: form, filing date, 10b5-1 box and every trade
    (table, security, date, code, acquired or disposed, shares, price, holdings after, direct or
    indirect)."""
    checks = []
    for accession in sorted(set(ours) | set(theirs)):
        mine, data_set = ours.get(accession), theirs.get(accession)
        if mine is None and data_set is not None:
            checks.append(
                FilingCheck(
                    accession, data_set.issuer_cik, "only_data_set", len(data_set.lines), ()
                )
            )
            continue
        if mine is None:
            continue
        if data_set is None:
            checks.append(FilingCheck(accession, mine.issuer_cik, "only_ours", len(mine.lines), ()))
            continue
        differences = list(_header_differences(mine, data_set))
        ours_lines = Counter(_trade(line) for line in mine.lines)
        their_lines = Counter(_trade(line) for line in data_set.lines)
        for trade in sorted((ours_lines - their_lines).elements(), key=str):
            differences.append(f"only in our parse: {_describe(trade)}")
        for trade in sorted((their_lines - ours_lines).elements(), key=str):
            differences.append(f"only in the data set: {_describe(trade)}")
        status: Status = "differs" if differences else "same"
        checks.append(
            FilingCheck(accession, mine.issuer_cik, status, len(mine.lines), tuple(differences))
        )
    return checks


def _header_differences(mine: OurFiling, data_set: DataSetFiling) -> Iterator[str]:
    for name, ours, theirs in (
        ("form", mine.form, data_set.form),
        ("filing date", mine.filed_at, data_set.filed_at),
        ("10b5-1 box", mine.rule_10b5_1, data_set.rule_10b5_1),
    ):
        if ours != theirs:
            yield f"{name}: ours {ours}, the data set's {theirs}"


type Trade = tuple[
    bool, str, date, str, str | None, Decimal | None, Decimal | None, Decimal | None, str | None
]


def _trade(line: TradeLine | DataSetLine) -> Trade:
    return (
        line.is_derivative,
        " ".join((line.security_title or "").lower().split()),
        line.trade_date,
        line.code,
        line.acquired_disposed,
        line.shares,  # equal amounts are equal whatever their trailing zeros (4012678.0)
        line.price,
        line.owned_after,
        line.direct_indirect,
    )


def _describe(trade: Trade) -> str:
    derivative, title, day, code, direction, shares, price, owned, ownership = trade
    table = "table II" if derivative else "table I"
    return (
        f"{table} {title or '(no title)'} {day} {code} {direction or '-'} shares {shares} "
        f"price {price} holdings after {owned} {ownership or '-'}"
    )


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    try:
        member = archive.open(name)
    except KeyError:
        raise SecFileError(f"the insider data set has no {name}") from None
    with io.TextIOWrapper(member, encoding="utf-8", newline="") as text:
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None:
            return
        for values in reader:
            if len(values) != len(header):
                raise SecFileError(f"{name}: a row has {len(values)} fields, not {len(header)}")
            yield dict(zip(header, values, strict=True))


def _line(row: Mapping[str, str], derivative: bool) -> DataSetLine:
    return DataSetLine(
        is_derivative=derivative,
        security_title=row["SECURITY_TITLE"] or None,
        trade_date=_date(row["TRANS_DATE"]),
        code=row["TRANS_CODE"].upper(),
        acquired_disposed=row["TRANS_ACQUIRED_DISP_CD"].upper() or None,
        shares=_decimal(row["TRANS_SHARES"]),
        price=_decimal(row["TRANS_PRICEPERSHARE"]),
        owned_after=_decimal(row["SHRS_OWND_FOLWNG_TRANS"]),
        direct_indirect=row["DIRECT_INDIRECT_OWNERSHIP"].upper() or None,
    )


def _date(text: str) -> date:
    try:
        return datetime.strptime(text, "%d-%b-%Y").date()  # noqa: DTZ007 - a calendar day
    except ValueError:
        raise SecFileError(f"the insider data set has a date written {text!r}") from None


def _decimal(text: str) -> Decimal | None:
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise SecFileError(f"the insider data set has a number written {text!r}") from None


def _flag(text: str) -> bool | None:
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    return None
