"""13F ingest for curated managers (E9-S2): one report, from its documents to stored lines.

1. The manager's listing (`filings`, routed by the watcher or queued by the funds backfill)
   names the filing; the manager must be in `managers`.
2. The filing's index page (raw first, D-070) gives each document's type: the cover is the
   13F-HR's XML (`primary_doc.xml`), the information table the XML of type INFORMATION TABLE,
   which the filer names. Both are fetched raw first, or read from the raw store when stored
   before. A report with no holdings has no table.
3. normalize/form13f.py parses them; the cover must be the listing manager's and of the
   listing's quarter. The values' unit is decided by the filing date (2023-01-03), unless an
   override in data/f13_overrides.toml pins the filer's (normalize/holdings.py).
4. The report and its lines are stored in one transaction.
"""

from dataclasses import dataclass
from pathlib import Path

import structlog

from marketdata.db.repositories import filings, holdings, managers
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.filing_index import IndexDocument, parse_filing_index
from marketdata.normalize.filings import F13_FORMS
from marketdata.normalize.form13f import (
    METHOD,
    PARSER_VERSION,
    parse_cover,
    parse_information_table,
)
from marketdata.normalize.holdings import (
    F13Record,
    UnitOverride,
    UnitOverrideError,
    UnitRule,
    parse_unit_overrides,
    value_unit,
)
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


class F13Ingest:
    def __init__(
        self, fetcher: RawFetcher, connect: Connect, overrides: tuple[UnitOverride, ...] = ()
    ) -> None:
        self._fetcher = fetcher
        self._connect = connect
        self._overrides = overrides

    async def run(self, cik: int, accession_no: str) -> F13Report:
        """Fetch, parse and store the 13F-HR or 13F-HR/A `accession_no` from `cik`'s listing."""
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
            await holdings.store(conn, record, method=METHOD, version=PARSER_VERSION)
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
        )
        return report


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
