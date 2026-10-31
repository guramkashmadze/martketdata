"""Form 4 ingest (E8): one insider filing, from its XML to stored trades.

1. The issuer's listing (`filings`, routed by the watcher or queued by the insiders backfill)
   names the primary document, EDGAR's rendering of the XML; the XML is the same file name
   without the `xsl…/` folder (PLAN.md §8). It is fetched raw first, or read from the raw store
   when stored before (D-070). When that name isn't in the folder, the folder's index.json
   lists the XML.
2. normalize/form4.py parses it; its issuer must be the listing's company (D-063: the issuer's
   listing has no file number, an owner's has the issuer's).
3. The filing, its owners and its trades are stored in one transaction.
"""

from dataclasses import dataclass

import structlog

from marketdata.db.repositories import filings, insiders
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.form4 import (
    METHOD,
    PARSER_VERSION,
    parse_form4,
    xml_document_name,
    xml_names_in_index,
)
from marketdata.normalize.insiders import FilingRecord
from marketdata.normalize.security_master import SecFileError
from marketdata.sec import urls
from marketdata.sec.client import SecNotFound

log = structlog.get_logger(__name__)


class Form4Refused(Exception):
    """The filing can't be stored from this listing, whatever is retried: no such listing, or
    a document whose issuer is another company."""


@dataclass(frozen=True)
class Form4Report:
    accession_no: str
    cik: int
    form: str
    owners: int
    lines: int
    raw_key: str
    fetched: bool  # False: the XML was read from the raw store


class Form4Ingest:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def run(self, cik: int, accession_no: str) -> Form4Report:
        """Fetch, parse and store the Form 4 or 4/A `accession_no` from `cik`'s listing."""
        accession_no = urls.accession_no(accession_no)
        async with self._connect() as conn:
            listed = [
                listing
                for listing in await filings.listings(conn, [accession_no])
                if listing.cik == cik
            ]
        if not listed:
            raise Form4Refused(f"{accession_no} is not listed under CIK {cik}")
        listing = listed[0]
        doc_name, raw_key, data, fetched = await self._xml(cik, accession_no, listing.primary_doc)
        form4 = parse_form4(data, accession_no)
        if form4.issuer_cik != cik:
            raise Form4Refused(
                f"{accession_no}: the Form 4 is about CIK {form4.issuer_cik}, not the listing's "
                f"company {cik}"
            )
        if form4.document_type != listing.form:
            log.warning(
                "form4_type_differs",
                cik=cik,
                accession_no=accession_no,
                listed=listing.form,
                document=form4.document_type,
            )
        record = FilingRecord(accession_no, listing.filed_at, doc_name, raw_key, form4)
        async with self._connect() as conn, conn.transaction():
            await insiders.store(conn, record, method=METHOD, version=PARSER_VERSION)
        report = Form4Report(
            accession_no=accession_no,
            cik=cik,
            form=form4.document_type,
            owners=len(form4.owners),
            lines=len(form4.lines),
            raw_key=raw_key,
            fetched=fetched,
        )
        log.info(
            "form4_stored",
            cik=cik,
            accession_no=accession_no,
            form=report.form,
            owners=report.owners,
            lines=report.lines,
            fetched=fetched,
        )
        return report

    async def _xml(
        self, cik: int, accession_no: str, primary_doc: str | None
    ) -> tuple[str, str, bytes, bool]:
        """The filing's XML: its name, raw key, bytes and whether it was fetched now."""
        if primary_doc:
            name = xml_document_name(primary_doc)
            try:
                url = urls.filing_document(cik, accession_no, name)
                raw_key, data, fetched = await self._fetcher.fetch_document(url)
                return name, raw_key, data, fetched
            except (SecNotFound, ValueError) as exc:
                log.warning(
                    "form4_xml_not_at_name",
                    cik=cik,
                    accession_no=accession_no,
                    name=name,
                    error=str(exc),
                )
        return await self._xml_from_index(cik, accession_no)

    async def _xml_from_index(self, cik: int, accession_no: str) -> tuple[str, str, bytes, bool]:
        _, index, _ = await self._fetcher.fetch_document(urls.filing_index_json(cik, accession_no))
        for name in xml_names_in_index(index, accession_no):
            raw_key, data, fetched = await self._fetcher.fetch_document(
                urls.filing_document(cik, accession_no, name)
            )
            try:
                parse_form4(data, accession_no)
            except SecFileError:
                continue
            return name, raw_key, data, fetched
        raise Form4Refused(f"{accession_no}: the filing folder has no Form 4 XML")
