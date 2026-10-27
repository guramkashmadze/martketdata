"""Filings from submissions JSON: parse them, check their acceptance times, plan what to store
and route work to later epics (PLAN.md §4, §8; E3-S4, S5).

Pure: functions take the bytes of SEC files and stored values and return records and plans;
ingest/watcher.py fetches the files (raw store first) and writes.

- A submissions file lists a company's filings in columns: `filings.recent` holds at least a
  year, and older filings are on the pages listed in `filings.files`, whose columns sit at the
  top level.
- One filing can be listed under several companies, each listing with its own file number
  (D-060). On a Schedule 13D or 13G the subject company's listing has a `005-` file number and
  the filer's has none. Forms 3, 4 and 5 are the other way round: the issuer's listing has none
  and a reporting owner's has the issuer's file number.
- `acceptanceDateTime` is UTC and right, except when the SEC serves a whole file with every
  time shifted by the Eastern UTC offset a second time (AAPL on 2026-10-24: 4 hours late in
  summer, 5 in winter). `check_acceptance_times` catches such a file: its new filings look
  accepted in the future, and many of its filings outside EDGAR's hours. A file that fails
  gives no acceptance times (null), never a guessed correction (D-057).
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from marketdata.normalize.security_master import FilerType, SecFileError

EASTERN = ZoneInfo("America/New_York")

# Task kinds filings are routed to (PLAN.md §4). Each runs once its epic registers a handler.
FACTS_REFRESH = "facts_refresh"  # E4a, key: CIK
EARNINGS_EXTRACT = "earnings_extract"  # E7a, key: accession number
FORM4_PARSE = "form4_parse"  # E8, key: accession number
F13_PARSE = "f13_parse"  # E9, key: accession number

# The 10-K and 10-Q family (with transition reports, D-052), whose XBRL facts E4a loads.
PERIODIC_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A"}
)
EARNINGS_FORMS = frozenset({"8-K", "8-K/A"})
INSIDER_FORMS = frozenset({"4", "4/A"})
F13_FORMS = frozenset({"13F-HR", "13F-HR/A"})

# The acceptance-time check (D-057). EDGAR accepts filings from 06:00 to 22:00 Eastern; a
# transmission begun before 22:00 can be accepted a few minutes after. Filings the SEC makes
# itself (accession numbers starting 99999999: EFFECT, NO ACT) come at any hour, and filings
# before 2005 can carry midnight placeholders, so the hours rule skips both.
EDGAR_OPENS = time(6, 0)
EDGAR_CLOSES = time(22, 15)
SEC_GENERATED_PREFIX = "99999999"
HOURS_RULE_SINCE = date(2005, 1, 1)
MAX_OUT_OF_HOURS_SHARE = 0.05
MIN_OUT_OF_HOURS = 3
CLOCK_SKEW = timedelta(minutes=5)

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "acceptanceDateTime",
    "form",
    "fileNumber",
    "items",
    "isXBRL",
    "primaryDocument",
)
_REQUIRED = ("accessionNumber", "filingDate", "form")


@dataclass(frozen=True)
class FilingRow:
    """A filing as one company's submissions list it (a `filings` row)."""

    accession_no: str
    cik: int  # the listing company
    form: str
    filed_at: date
    accepted_at: datetime | None
    period_of_report: date | None
    items_8k: tuple[str, ...] | None  # 8-K forms only, e.g. ("2.02", "9.01")
    primary_doc: str | None
    is_xbrl: bool | None
    file_number: str | None  # this listing's


@dataclass(frozen=True)
class SubmissionsPage:
    """An older page listed in `filings.files`."""

    name: str  # e.g. CIK0000320193-submissions-001.json
    filing_count: int
    filed_from: date
    filed_to: date


@dataclass(frozen=True)
class Submissions:
    cik: int
    name: str
    filings: tuple[FilingRow, ...]  # filings.recent
    pages: tuple[SubmissionsPage, ...]


def parse_submissions(data: bytes) -> Submissions:
    """The filings in `filings.recent` and the older pages a submissions file lists."""
    document = _json(data, "submissions JSON")
    cik, name = document.get("cik"), document.get("name")
    if not (isinstance(cik, str) and cik.isdigit() and int(cik) > 0):
        raise SecFileError(f"submissions JSON has no usable cik: {cik!r}")
    filings = document.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise SecFileError(f"submissions JSON for CIK {int(cik)} has no filings.recent")
    pages = filings.get("files") if isinstance(filings, dict) else None
    return Submissions(
        cik=int(cik),
        name=name.strip() if isinstance(name, str) else "",
        filings=_rows(recent, int(cik), f"submissions JSON for CIK {int(cik)}"),
        pages=_pages(pages if isinstance(pages, list) else [], int(cik)),
    )


def parse_submissions_page(data: bytes, cik: int) -> tuple[FilingRow, ...]:
    """The filings on an older submissions page of `cik`."""
    return _rows(_json(data, "submissions page"), cik, f"submissions page of CIK {cik}")


@dataclass(frozen=True)
class AcceptanceCheck:
    """Whether a file's acceptance times can be trusted (D-057)."""

    future: int  # acceptance times after the file was fetched
    out_of_hours: int  # filings the hours rule covers, accepted outside EDGAR's hours
    covered: int  # filings the hours rule covers

    @property
    def trusted(self) -> bool:
        limit = max(MIN_OUT_OF_HOURS, MAX_OUT_OF_HOURS_SHARE * self.covered)
        return self.future == 0 and self.out_of_hours <= limit

    @property
    def reason(self) -> str | None:
        if self.future:
            return f"{self.future} acceptance times are later than the file was fetched"
        if not self.trusted:
            return (
                f"{self.out_of_hours} of {self.covered} filings were accepted outside EDGAR's "
                "hours (06:00-22:00 Eastern): every time looks shifted"
            )
        return None


def check_acceptance_times(rows: Sequence[FilingRow], fetched_at: datetime) -> AcceptanceCheck:
    future = sum(
        1
        for row in rows
        if row.accepted_at is not None and row.accepted_at > fetched_at + CLOCK_SKEW
    )
    covered = out_of_hours = 0
    for row in rows:
        if (
            row.accepted_at is None
            or row.accession_no.startswith(SEC_GENERATED_PREFIX)
            or row.filed_at < HOURS_RULE_SINCE
        ):
            continue
        covered += 1
        clock = row.accepted_at.astimezone(EASTERN).time()
        if not EDGAR_OPENS <= clock < EDGAR_CLOSES:
            out_of_hours += 1
    return AcceptanceCheck(future=future, out_of_hours=out_of_hours, covered=covered)


def without_acceptance_times(rows: Sequence[FilingRow]) -> tuple[FilingRow, ...]:
    return tuple(replace(row, accepted_at=None) for row in rows)


@dataclass(frozen=True)
class AcceptanceFill:
    """A stored listing without an acceptance time that this file gives one."""

    accession_no: str
    accepted_at: datetime


@dataclass(frozen=True)
class AcceptanceDiffers:
    """A stored acceptance time this file disagrees with; the stored one is kept."""

    accession_no: str
    stored: datetime
    listed: datetime


@dataclass(frozen=True)
class FilingsPlan:
    new: tuple[FilingRow, ...]
    fill: tuple[AcceptanceFill, ...]
    differ: tuple[AcceptanceDiffers, ...]


def plan_filings(stored: Mapping[str, datetime | None], listed: Sequence[FilingRow]) -> FilingsPlan:
    """What a file adds to one company's stored listings (accession number to acceptance time).

    A stored value never changes: a missing acceptance time is filled, a different one is only
    reported. A file listing an accession twice counts its first row.
    """
    new: list[FilingRow] = []
    fill: list[AcceptanceFill] = []
    differ: list[AcceptanceDiffers] = []
    seen: set[str] = set()
    for row in listed:
        if row.accession_no in seen:
            continue
        seen.add(row.accession_no)
        if row.accession_no not in stored:
            new.append(row)
            continue
        known = stored[row.accession_no]
        if row.accepted_at is None:
            continue
        if known is None:
            fill.append(AcceptanceFill(row.accession_no, row.accepted_at))
        elif known != row.accepted_at:
            differ.append(AcceptanceDiffers(row.accession_no, known, row.accepted_at))
    return FilingsPlan(tuple(new), tuple(fill), tuple(differ))


@dataclass(frozen=True)
class Route:
    """A task to enqueue for a new filing. `force` queues a finished task with the same key
    again: a facts refresh is keyed by company, so every new 10-K or 10-Q must run it anew
    (D-058, D-081)."""

    kind: str
    key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    force: bool = False


def route(
    filing: FilingRow, *, filer_type: FilerType, curated_manager: bool = False
) -> list[Route]:
    """§4's task routing for a newly seen filing. Earnings and facts are produced for domestic
    filers only (§9.1); Form 4 is parsed from the issuer's listing (E8); 13F only for curated
    managers (E9)."""
    accession = {"cik": filing.cik, "accession_no": filing.accession_no}
    if filing.form in PERIODIC_FORMS and filer_type == "domestic":
        return [Route(FACTS_REFRESH, str(filing.cik), {"cik": filing.cik}, force=True)]
    if (
        filing.form in EARNINGS_FORMS
        and "2.02" in (filing.items_8k or ())
        and filer_type == "domestic"
    ):
        return [Route(EARNINGS_EXTRACT, filing.accession_no, accession)]
    if filing.form in INSIDER_FORMS and filing.file_number is None:
        return [Route(FORM4_PARSE, filing.accession_no, accession)]
    if filing.form in F13_FORMS and curated_manager:
        return [Route(F13_PARSE, filing.accession_no, accession)]
    return []


def _json(data: bytes, what: str) -> dict[str, Any]:
    try:
        document = json.loads(data)
    except ValueError as exc:
        raise SecFileError(f"{what} is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise SecFileError(f"{what}: expected an object")
    return document


def _rows(columns: Mapping[str, Any], cik: int, where: str) -> tuple[FilingRow, ...]:
    missing = [name for name in _REQUIRED if not isinstance(columns.get(name), list)]
    if missing:
        raise SecFileError(f"{where} has no column {', '.join(missing)}")
    count = len(columns["accessionNumber"])
    present = {}
    for name in _COLUMNS:
        values = columns.get(name)
        if isinstance(values, list):
            if len(values) != count:
                raise SecFileError(f"{where}: column {name} has {len(values)} rows, not {count}")
            present[name] = values
    rows = []
    for index in range(count):
        value = {name: values[index] for name, values in present.items()}
        rows.append(_row(value, cik, where, index))
    return tuple(rows)


def _row(value: Mapping[str, Any], cik: int, where: str, index: int) -> FilingRow:
    accession, form = value["accessionNumber"], value["form"]
    if not (isinstance(accession, str) and _ACCESSION.match(accession)):
        raise SecFileError(f"{where} row {index}: bad accession number {accession!r}")
    if not (isinstance(form, str) and form.strip()):
        raise SecFileError(f"{where} row {index} ({accession}): no form")
    filed = _date(value["filingDate"])
    if filed is None:
        raise SecFileError(f"{where} row {index} ({accession}): bad filingDate")
    form = form.strip()
    return FilingRow(
        accession_no=accession,
        cik=cik,
        form=form,
        filed_at=filed,
        accepted_at=_moment(value.get("acceptanceDateTime")),
        period_of_report=_date(value.get("reportDate")),
        items_8k=_items(value.get("items")) if form.startswith("8-K") else None,
        primary_doc=_text(value.get("primaryDocument")),
        is_xbrl=bool(value["isXBRL"]) if isinstance(value.get("isXBRL"), int) else None,
        file_number=_text(value.get("fileNumber")),
    )


def _pages(entries: list[Any], cik: int) -> tuple[SubmissionsPage, ...]:
    pages = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise SecFileError(f"submissions JSON for CIK {cik}: bad filings.files entry")
        name, count = entry.get("name"), entry.get("filingCount")
        filed_from, filed_to = _date(entry.get("filingFrom")), _date(entry.get("filingTo"))
        if (
            not isinstance(name, str)
            or not isinstance(count, int)
            or not filed_from
            or not filed_to
        ):
            raise SecFileError(f"submissions JSON for CIK {cik}: bad filings.files entry {entry}")
        pages.append(SubmissionsPage(name, count, filed_from, filed_to))
    return tuple(pages)


def _items(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(code.strip() for code in value.split(",") if code.strip())


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None
