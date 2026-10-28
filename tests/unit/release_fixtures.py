"""E7a: releases built from the committed filings (tests/fixtures/sec/filings/{cik}/{accession}),
as the ingest builds them from the raw store: the index page's EX-99 exhibits in the order
PLAN.md §9.9 tries them, each parsed into its text and tables."""

import gzip
from collections.abc import Mapping
from pathlib import Path

from marketdata.extract.html_tables import parse_document
from marketdata.extract.interface import Release, ReleaseDocument, Slot
from marketdata.normalize.filing_index import earnings_exhibits, parse_filing_index

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"

# Fiscal year ends ('MMDD') of the fixture companies, as their submissions JSON gives them.
FISCAL_YEAR_ENDS = {
    320193: "0926",  # Apple
    27419: "0201",  # Target
    1397187: "0131",  # lululemon
    19617: "1231",  # JPMorgan
    909832: "0830",  # Costco
    34088: "1231",  # Exxon Mobil Corp (XOM's predecessor)
    2115436: "1231",  # ExxonMobil Holdings Corp (XOM)
    1594805: "1231",  # Shopify
    1403161: "0930",  # Visa
    51143: "1231",  # IBM
    21344: "1231",  # Coca-Cola
    1108524: "0131",  # Salesforce
    723125: "0902",  # Micron
    104169: "0131",  # Walmart
    315189: "1101",  # Deere
    789019: "0630",  # Microsoft
    1045810: "0131",  # NVIDIA
    753308: "1231",  # NextEra
    1067983: "1231",  # Berkshire Hathaway
    86312: "1231",  # Travelers
    200406: "0103",  # Johnson & Johnson
    1874178: "1231",  # Rivian
    1326801: "1231",  # Meta
    1392380: "1231",  # Gevo
    1467373: "0831",  # Accenture
}


def document(cik: int, accession: str, name: str) -> bytes:
    path = FILINGS / str(cik) / accession / name
    if path.exists():
        return path.read_bytes()
    return gzip.decompress((FILINGS / str(cik) / accession / f"{name}.gz").read_bytes())


def fixture_release(
    cik: int, accession: str, *, not_applicable: Mapping[Slot, str] | None = None
) -> Release:
    index = parse_filing_index(document(cik, accession, f"{accession}-index.htm"))
    documents = []
    for exhibit in earnings_exhibits(index):
        parsed = parse_document(exhibit.name, document(cik, accession, exhibit.name))
        documents.append(
            ReleaseDocument(
                exhibit.name, exhibit.doc_type, parsed.text, parsed.tables, parsed.images
            )
        )
    return Release(
        cik,
        accession,
        index.form,
        tuple(documents),
        not_applicable or {},
        index.filed_at,
        FISCAL_YEAR_ENDS.get(cik),
    )
