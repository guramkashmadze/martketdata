"""The documents table (PLAN.md §7, E7a-S1): the text of each exhibit an earnings task stored.

The raw HTML stays in the raw store (raw_key); `text` has the tables as markdown rows, and is
what verification reads (§9.9). A new version of the HTML-to-text rules rewrites the text.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    doc_name: str
    doc_type: str
    raw_key: str
    text: str
    extracted_at: datetime | None = None  # set by the database


_UPSERT = """
    insert into documents (accession_no, doc_name, doc_type, raw_key, text)
    values (%(accession_no)s, %(doc_name)s, %(doc_type)s, %(raw_key)s, %(text)s)
    on conflict (accession_no, doc_name) do update set
      doc_type = excluded.doc_type,
      raw_key = excluded.raw_key,
      text = excluded.text,
      extracted_at = now()
    where (documents.doc_type, documents.raw_key, documents.text)
      is distinct from (excluded.doc_type, excluded.raw_key, excluded.text)
"""

_FOR_FILING = """
    select accession_no, doc_name, doc_type, raw_key, text, extracted_at from documents
    where accession_no = %(accession_no)s
    order by doc_name
"""

_GET = """
    select accession_no, doc_name, doc_type, raw_key, text, extracted_at from documents
    where accession_no = %(accession_no)s and doc_name = %(doc_name)s
"""


async def upsert(conn: AsyncConnection[Any], documents: Sequence[Document]) -> int:
    """Store these documents; a document whose text, type and raw file are unchanged is left as
    it is. Returns how many rows were written."""
    written = 0
    async with conn.cursor() as cur:
        for document in documents:
            await cur.execute(_UPSERT, document.model_dump(exclude={"extracted_at"}))
            written += cur.rowcount
    return written


async def for_filing(conn: AsyncConnection[Any], accession_no: str) -> list[Document]:
    async with conn.cursor(row_factory=class_row(Document)) as cur:
        await cur.execute(_FOR_FILING, {"accession_no": accession_no})
        return await cur.fetchall()


async def get(conn: AsyncConnection[Any], accession_no: str, doc_name: str) -> Document | None:
    async with conn.cursor(row_factory=class_row(Document)) as cur:
        await cur.execute(_GET, {"accession_no": accession_no, "doc_name": doc_name})
        return await cur.fetchone()
