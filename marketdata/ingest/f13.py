"""13F ingest for curated managers (E9-S2, S4): one report, from its documents to positions.

1. The manager's listing (`filings`, routed by the watcher or queued by the funds backfill)
   names the filing; the manager must be in `managers`.
2. The filing's index page (raw first, D-070) gives each document's type: the cover is the
   13F-HR's XML (`primary_doc.xml`), the information table the XML of type INFORMATION TABLE,
   which the filer names. Both are fetched raw first, or read from the raw store when stored
   before. A report with no holdings has no table.
3. normalize/form13f.py parses them; the cover must be the listing manager's and of the
   listing's quarter. The values' unit is decided by the filing date (2023-01-03), unless an
   override in data/f13_overrides.toml pins the filer's (normalize/holdings.py).
4. The report and its lines are stored in one transaction, which builds the manager's quarter
   again from all its reports (normalize/holdings.py: amendments in filing order, lines merged
   by CUSIP and put or call, weights), under a lock per manager and quarter. Each stored
   position whose shares or value change is logged in `value_changes` and announced as
   `data.changed` (D-027): an amendment's changes, or a correction when the same report is
   parsed again with another parser version or unit. A report routed when filed queues the
   mapping of its CUSIPs (`cusip_map`, ingest/cusips.py).
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import filings, holdings, managers, tasks, value_changes
from marketdata.db.repositories.holdings import StoredF13Filing
from marketdata.ingest.announce import announce
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.changes import Reason
from marketdata.normalize.cusips import CUSIP_MAP, PENDING
from marketdata.normalize.filing_index import IndexDocument, parse_filing_index
from marketdata.normalize.filings import F13_FORMS, F13_PARSE
from marketdata.normalize.form13f import (
    METHOD,
    PARSER_VERSION,
    parse_cover,
    parse_information_table,
)
from marketdata.normalize.holdings import (
    F13Record,
    FiledLine,
    PeriodFiling,
    ServedPosition,
    UnitOverride,
    UnitOverrideError,
    UnitRule,
    lines_in_force,
    parse_unit_overrides,
    position_changes,
    positions,
    value_unit,
)
from marketdata.normalize.webhooks import DATA_CHANGED
from marketdata.sec import urls

log = structlog.get_logger(__name__)

INFORMATION_TABLE = "INFORMATION TABLE"


def read_unit_overrides(path: Path) -> tuple[UnitOverride, ...]:
    """The 13F unit overrides file (F13_OVERRIDES_FILE), checked: each source_url must be an SEC
    filing page of the filer. A missing file means no overrides; UnitOverrideError lists every
    problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    overrides = parse_unit_overrides(text, str(path))
    problems = []
    for number, override in enumerate(overrides, start=1):
        parsed = urls.parse(override.source_url)
        if parsed is None or parsed.endpoint != "filing" or parsed.cik != override.manager_cik:
            problems.append(
                f"unit {number} (CIK {override.manager_cik}): source_url is not a filing page of "
                f"the filer: {override.source_url}"
            )
    if problems:
        raise UnitOverrideError(f"{path}:\n" + "\n".join(f"  - {line}" for line in problems))
    return overrides


class F13Refused(Exception):
    """The report can't be stored from this listing, whatever is retried: no such listing, a
    filer that isn't a manager, or documents that aren't the listing's 13F."""


@dataclass(frozen=True)
class F13Report:
    accession_no: str
    manager_cik: int
    form: str
    report_period: str
    lines: int
    unit: UnitRule
    entries_match: bool | None  # the table has the summary page's line count (None: none given)
    totals_match: bool | None  # the values add up to the summary page's total
    fetched: int  # documents fetched now; the others were read from the raw store
    positions: int  # the manager's positions at the quarter end, after this report
    changes: int  # stored positions this run changed (value_changes rows)


class F13Ingest:
    def __init__(
        self, fetcher: RawFetcher, connect: Connect, overrides: tuple[UnitOverride, ...] = ()
    ) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._overrides = overrides

    async def run(self, cik: int, accession_no: str, *, live: bool = False) -> F13Report:
        """Fetch, parse and store the 13F-HR or 13F-HR/A `accession_no` from `cik`'s listing.
        A `live` report (routed when filed) queues the mapping of its new CUSIPs."""
        accession_no = urls.accession_no(accession_no)
        async with self._connect() as conn:
            listed = [
                listing
                for listing in await filings.listings(conn, [accession_no])
                if listing.cik == cik
            ]
            manager = await managers.get(conn, cik)
        if not listed:
            raise F13Refused(f"{accession_no} is not listed under CIK {cik}")
        listing = listed[0]
        if listing.form not in F13_FORMS:
            raise F13Refused(f"{accession_no} is a {listing.form}, not a 13F-HR")
        if manager is None:
            raise F13Refused(f"CIK {cik} is not a manager: run `marketdata managers load`")
        index_url = urls.filing_index_page(cik, accession_no)
        _, index_page, index_fetched = await self._fetcher.fetch_document(index_url)
        index = parse_filing_index(index_page)
        cover_doc, table_doc = _documents(index.documents, listing.form, accession_no)
        cover_key, cover_data, cover_fetched = await self._fetcher.fetch_document(
            urls.filing_document(cik, accession_no, cover_doc)
        )
        cover = parse_cover(cover_data, f"{accession_no} cover")
        if cover.filer_cik is not None and cover.filer_cik != cik:
            raise F13Refused(
                f"{accession_no}: the cover is CIK {cover.filer_cik}'s, not the listing's {cik}"
            )
        table_key, table_fetched, lines = None, False, []
        if table_doc is not None:
            table_key, table_data, table_fetched = await self._fetcher.fetch_document(
                urls.filing_document(cik, accession_no, table_doc)
            )
            lines = parse_information_table(table_data, f"{accession_no} information table")
        elif cover.entry_total:
            raise F13Refused(
                f"{accession_no}: the summary page lists {cover.entry_total} lines but the "
                "filing has no information table"
            )
        if listing.period_of_report not in (None, cover.report_period):
            log.warning(
                "f13_period_differs",
                cik=cik,
                accession_no=accession_no,
                listed=str(listing.period_of_report),
                cover=str(cover.report_period),
            )
        if cover.submission_type != listing.form:
            log.warning(
                "f13_type_differs",
                cik=cik,
                accession_no=accession_no,
                listed=listing.form,
                cover=cover.submission_type,
            )
        record = F13Record(
            accession_no=accession_no,
            manager_cik=cik,
            form=listing.form,
            filed_at=listing.filed_at,
            cover=cover,
            holdings=tuple(lines),
            unit=value_unit(cik, listing.filed_at, self._overrides),
            cover_doc=cover_doc,
            cover_raw_key=cover_key,
            table_doc=table_doc,
            table_raw_key=table_key,
        )
        async with self._connect() as conn, conn.transaction():
            await holdings.lock_period(conn, cik, cover.report_period)
            previous = await holdings.get_filing(conn, accession_no)
            await holdings.store(conn, record, method=METHOD, version=PARSER_VERSION)
            built, changes = await _build_quarter(conn, record, previous)
            if live:
                await tasks.enqueue(conn, CUSIP_MAP, PENDING, {}, force=True)
        report = F13Report(
            accession_no=accession_no,
            manager_cik=cik,
            form=listing.form,
            report_period=cover.report_period.isoformat(),
            lines=len(lines),
            unit=record.unit,
            entries_match=None if cover.entry_total is None else cover.entry_total == len(lines),
            totals_match=(
                None if cover.value_total is None else cover.value_total == record.value_sum
            ),
            fetched=sum((index_fetched, cover_fetched, table_fetched)),
            positions=built,
            changes=changes,
        )
        log.info(
            "f13_stored",
            cik=cik,
            accession_no=accession_no,
            form=report.form,
            report_period=report.report_period,
            amendment_type=cover.amendment_type,
            lines=report.lines,
            unit=record.unit.unit,
            entries_match=report.entries_match,
            totals_match=report.totals_match,
            fetched=report.fetched,
            positions=built,
            changes=changes,
        )
        return report


async def _build_quarter(
    conn: AsyncConnection[Any], record: F13Record, previous: StoredF13Filing | None
) -> tuple[int, int]:
    """Build the manager's quarter again from all its stored reports; log and announce the
    stored positions that change. Returns the positions and the changes."""
    cik, period = record.manager_cik, record.cover.report_period
    stored_filings = await holdings.period_filings(conn, cik, period)
    lines = await holdings.lines(conn, [filing.accession_no for filing in stored_filings])
    by_filing: dict[str, list[FiledLine]] = {}
    for line in lines:
        by_filing.setdefault(line.accession_no, []).append(
            FiledLine(
                line.accession_no,
                line.line_no,
                line.cusip,
                line.put_call,
                line.share_type,
                line.name_of_issuer,
                line.title_of_class,
                line.figi,
                line.shares,
                line.value_as_filed,
                line.value_usd,
                line.other_managers,
            )
        )
    in_force = lines_in_force(
        [
            PeriodFiling(
                filing.accession_no,
                filing.form,
                filing.filed_at,
                filing.amendment_type,
                tuple(by_filing.get(filing.accession_no, ())),
            )
            for filing in stored_filings
        ]
    )
    if in_force.repeated:
        log.warning(
            "f13_new_holdings_repeated",
            cik=cik,
            report_period=period.isoformat(),
            lines=[f"{accession} line {line_no}" for accession, line_no in in_force.repeated],
        )
    built = positions(in_force)
    stored = [
        ServedPosition(position.cusip, position.put_call, position.shares, position.value_usd)
        for position in await holdings.positions(conn, cik, period)
    ]
    reason, detail = _reason(record, previous)
    found = position_changes(cik, period, stored, built, reason, detail)
    await holdings.replace_positions(conn, cik, period, built)
    for change_id in await value_changes.record_position_changes(conn, cik, found):
        await announce(conn, DATA_CHANGED, str(change_id))
    return len(built), len(found)


def _reason(record: F13Record, previous: StoredF13Filing | None) -> tuple[Reason, str]:
    """Why a stored position changes: the same report parsed again (a correction), or another
    report of the quarter (an amendment)."""
    period = record.cover.report_period.isoformat()
    if previous is not None:
        if previous.extractor_version != PARSER_VERSION:
            what = f"parser version {previous.extractor_version} to {PARSER_VERSION}"
        elif previous.value_unit != record.unit.unit:
            what = (
                f"values read in {record.unit.unit}, not {previous.value_unit} "
                f"({record.unit.rule})"
            )
        else:
            what = "the report was parsed again"
        return "correction", f"{record.form} {record.accession_no} for {period}: {what}"
    if record.form == "13F-HR/A":
        kind = record.cover.amendment_type or "no amendment type"
        return "amendment", (
            f"13F-HR/A {record.accession_no} ({kind}), filed {record.filed_at}, amends the "
            f"report for {period}"
        )
    return "amendment", (
        f"13F-HR {record.accession_no}, filed {record.filed_at}, reports {period} again"
    )


def _documents(
    documents: tuple[IndexDocument, ...], form: str, accession_no: str
) -> tuple[str, str | None]:
    """The cover's XML and the information table's (None when the report has none), as the
    index page types them; EDGAR's renderings (.html) are skipped."""
    xml = [document for document in documents if document.name.lower().endswith(".xml")]
    covers = [document.name for document in xml if document.doc_type in (form, *F13_FORMS)]
    tables = [document.name for document in xml if document.doc_type == INFORMATION_TABLE]
    if not covers:
        raise F13Refused(f"{accession_no}: the index page lists no 13F cover XML")
    if len(tables) > 1:
        raise F13Refused(f"{accession_no}: the index page lists {len(tables)} information tables")
    return covers[0], tables[0] if tables else None


@dataclass(frozen=True)
class FundsBackfillReport:
    cik: int
    since: date
    queued: tuple[str, ...]  # accession numbers whose f13_parse task is queued


class FundsBackfill:
    """One curated manager's 13F reports back to a date (`marketdata backfill funds`, E9-S5).

    The manager's filings are loaded first: a manager never watched gets its first watch (which
    routes nothing, D-063) and its older pages back to the date. Then an `f13_parse` task is
    queued for each 13F-HR and 13F-HR/A on its listing filed in the window, behind live work
    (D-067), and one `cusip_map` task after them all. Each report's documents are fetched as
    for a live one: three SEC requests per report, none for documents stored before (D-070).
    """

    def __init__(self, fetcher: RawFetcher, connect: Connect, kinds: frozenset[str]) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._kinds = kinds  # registered task kinds (§4)

    async def run(self, cik: int, since: date, *, force: bool = False) -> FundsBackfillReport:
        """Queue `cik`'s 13F reports filed on or after `since`; `force` parses reports parsed
        before again (a new parser version)."""
        if F13_PARSE not in self._kinds:
            raise RuntimeError(f"{F13_PARSE} is not a registered task kind")
        async with self._connect() as conn:
            manager = await managers.get(conn, cik)
        if manager is None or not manager.curated:
            raise F13Refused(f"CIK {cik} is not a curated manager: run `marketdata managers load`")
        await FilingsWatcher(self._fetcher, self._connect, self._kinds).backfill(cik, since)
        queued: list[str] = []
        async with self._connect() as conn, conn.transaction():
            for filing in await filings.filer_listings(conn, cik, sorted(F13_FORMS), since):
                await tasks.enqueue(
                    conn,
                    F13_PARSE,
                    filing.accession_no,
                    {"cik": cik, "accession_no": filing.accession_no},
                    force=force,
                    priority=tasks.PRIORITY_BACKFILL,
                )
                queued.append(filing.accession_no)
            await tasks.enqueue(
                conn, CUSIP_MAP, PENDING, {}, force=True, priority=tasks.PRIORITY_BACKFILL - 1
            )
        log.info("funds_backfill_queued", cik=cik, since=since.isoformat(), queued=len(queued))
        return FundsBackfillReport(cik, since, tuple(queued))
