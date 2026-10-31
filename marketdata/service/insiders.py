"""Insider trades (PLAN.md §9.7, §10.2, E8): the trades of Form 4 filings, with proof.

A trade is served from the filing that states it now: a line a 4/A supersedes is served from the
4/A's line that corrects or restates it, and a filing made twice is served once (E8-S3).
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import insiders
from marketdata.db.repositories.insiders import StoredFiling, StoredLine
from marketdata.normalize.insider_data_set import OurFiling
from marketdata.normalize.insiders import LineKey
from marketdata.sec import urls


class InsiderProof(BaseModel):
    """Where a trade comes from (§9.10): the Form 4 or 4/A, its XML and the lines read."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str  # 4 or 4/A
    filed_at: date
    filing_url: str  # the filing's folder on the SEC's site
    document_url: str  # the XML the lines were read from
    lines: tuple[int, ...]  # the line numbers in the filing, table I first
    method: str  # form4_xml
    extractor_version: str


class ServedLine(BaseModel):
    """A line as it is served now, with its proof."""

    model_config = ConfigDict(frozen=True)

    line: StoredLine
    proof: InsiderProof


def proof_of(filing: StoredFiling, lines: Sequence[int], extractor_version: str) -> InsiderProof:
    return InsiderProof(
        accession_no=filing.accession_no,
        form=filing.form,
        filed_at=filing.filed_at,
        filing_url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
        document_url=urls.filing_document(filing.issuer_cik, filing.accession_no, filing.doc_name),
        lines=tuple(lines),
        method=filing.method,
        extractor_version=extractor_version,
    )


async def served_now(
    conn: AsyncConnection[Any], keys: Sequence[LineKey]
) -> dict[LineKey, ServedLine | None]:
    """What each of these lines serves now: itself, the line that supersedes it (following a
    later 4/A), or nothing (None) when a 4/A restated its filing without it."""
    found: dict[LineKey, ServedLine | None] = {}
    wanted = set(keys)
    stored: dict[LineKey, StoredLine] = {}
    for _ in range(8):  # a chain of amendments is short; a cycle would be a bug
        missing = sorted({key[0] for key in wanted if key not in stored})
        if not missing:
            break
        for line in await insiders.lines(conn, missing):
            stored[(line.accession_no, line.line_no)] = line
        wanted = {
            (line.superseded_by, line.superseded_line)
            for line in stored.values()
            if line.superseded_by is not None and line.superseded_line is not None
        } - set(stored)
    filings = {
        filing.accession_no: filing
        for filing in await insiders.filings(conn, sorted({key[0] for key in stored}))
    }
    for key in keys:
        serving = _serving(stored, key)
        filing = filings.get(serving.accession_no) if serving is not None else None
        found[key] = (
            ServedLine(
                line=serving,
                proof=proof_of(filing, [serving.line_no], serving.extractor_version),
            )
            if serving is not None and filing is not None
            else None
        )
    return found


def _serving(stored: dict[LineKey, StoredLine], key: LineKey) -> StoredLine | None:
    """The line that serves `key`'s trade now, following the lines that superseded it."""
    line = stored.get(key)
    for _ in range(8):
        if line is None or line.superseded_by is None:
            return line
        if line.superseded_line is None or line.superseded_reason == "duplicate":
            return None
        line = stored.get((line.superseded_by, line.superseded_line))
    return None


async def filed_in(
    conn: AsyncConnection[Any], ciks: Sequence[int], first: date, last: date
) -> dict[str, OurFiling]:
    """The Forms 4 about these issuers filed from `first` to `last` as we parsed them, every
    line as filed, superseded or not (E8-S4: compared with the SEC's data set)."""
    stored = await insiders.filed_between(conn, ciks, first, last)
    lines: dict[str, list[StoredLine]] = {filing.accession_no: [] for filing in stored}
    for line in await insiders.lines(conn, list(lines)):
        lines[line.accession_no].append(line)
    return {
        filing.accession_no: OurFiling(
            filing.accession_no,
            filing.issuer_cik,
            filing.form,
            filing.filed_at,
            filing.rule_10b5_1,
            lines[filing.accession_no],
        )
        for filing in stored
    }
