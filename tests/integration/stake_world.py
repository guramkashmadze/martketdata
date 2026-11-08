"""E16: real Schedule 13D and 13G filings for tests (fixtures/sec/filings/), listed on the subject
company's listing as the watcher stores them and stored through the real ingest."""

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from conftest import connect_to, filing_routes, sec_serving

from marketdata.db.repositories import filings
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.stakes import StakeIngest, StakeReport, StakesBackfill, StakesBackfillReport
from marketdata.normalize.filings import FilingRow
from marketdata.sec.raw_store import LocalRawStore

Conn = psycopg.AsyncConnection[Any]
AMD, ALPHABET, HUMACYTE, NEBIUS, NVIDIA, WALMART = 2488, 1652044, 1818382, 1513845, 1045810, 104169


@dataclass(frozen=True)
class Listed:
    """A filing as the subject company's submissions list it (file number 005-, D-061)."""

    cik: int
    accession_no: str
    form: str
    filed_at: date
    file_number: str | None
    primary_doc: str


VCM_AMD = Listed(
    AMD, "0002100119-26-000052", "SCHEDULE 13G", date(2026, 4, 29), "005-07443",
    "xslSCHEDULE_13G_X02/primary_doc.xml",
)  # fmt: skip
VANGUARD_AMD = Listed(
    AMD, "0000102909-26-000540", "SCHEDULE 13G/A", date(2026, 3, 26), "005-07443",
    "xslSCHEDULE_13G_X02/primary_doc.xml",
)  # fmt: skip
VCM_GOOG = Listed(
    ALPHABET, "0002100119-26-000137", "SCHEDULE 13G", date(2026, 4, 29), "005-89274",
    "xslSCHEDULE_13G_X02/primary_doc.xml",
)  # fmt: skip
VCM_GOOGL = Listed(
    ALPHABET, "0002100119-26-000161", "SCHEDULE 13G", date(2026, 4, 29), "005-89274",
    "xslSCHEDULE_13G_X02/primary_doc.xml",
)  # fmt: skip
SCHMIDT = Listed(
    ALPHABET, "0000950170-25-022699", "SCHEDULE 13G/A", date(2025, 2, 18), "005-89274",
    "xslSCHEDULE_13G_X01/primary_doc.xml",
)  # fmt: skip
FRESENIUS_JULY = Listed(
    HUMACYTE, "0001104659-26-083164", "SCHEDULE 13D/A", date(2026, 7, 13), "005-91701",
    "xslSCHEDULE_13D_X02/primary_doc.xml",
)  # fmt: skip
FRESENIUS_AUGUST = Listed(
    HUMACYTE, "0001104659-26-098152", "SCHEDULE 13D/A", date(2026, 8, 18), "005-91701",
    "xslSCHEDULE_13D_X02/primary_doc.xml",
)  # fmt: skip
WALTON = Listed(
    WALMART, "0001140361-24-049960", "SCHEDULE 13D", date(2024, 12, 19), "005-20101",
    "xslSCHEDULE_13D_X01/primary_doc.xml",
)  # fmt: skip
NVIDIA_NEBIUS = Listed(
    NEBIUS, "0001045810-26-000062", "SCHEDULE 13G", date(2026, 7, 20), "005-86274",
    "xslSCHEDULE_13G_X01/primary_doc.xml",
)  # fmt: skip


async def list_filing(conn: Conn, listed: Listed, **changes: Any) -> Listed:
    """Store the listing as the watcher would (changes replace its fields)."""
    row = replace(listed, **changes)
    await filings.insert_new(
        conn,
        [
            FilingRow(
                row.accession_no, row.cik, row.form, row.filed_at, None, None, None,
                row.primary_doc, None, row.file_number,
            )
        ],
    )  # fmt: skip
    return row


async def ingest(
    conn: Conn, tmp_path: Path, listed: Listed, routes: dict[str, bytes | int] | None = None
) -> StakeReport:
    async with sec_serving(filing_routes() if routes is None else routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await StakeIngest(fetcher, connect_to(conn)).run(listed.cik, listed.accession_no)


async def backfill(
    conn: Conn,
    tmp_path: Path,
    cik: int,
    *,
    force: bool = False,
    routes: dict[str, bytes | int] | None = None,
) -> StakesBackfillReport:
    async with sec_serving(filing_routes() if routes is None else routes) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        return await StakesBackfill(fetcher, connect_to(conn)).run(cik, force=force)
