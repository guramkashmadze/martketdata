"""The golden_values table (E5b-S4): the golden sets as data, replaced as a whole when
`marketdata quality accuracy` loads tests/golden/."""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from marketdata.normalize.golden import GoldenValue


class StoredGolden(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    cik: int
    accession_no: str | None
    slot: str | None
    metric: str | None
    period_type: str | None
    period_end: date | None
    value: Decimal | None
    prior_value: Decimal | None
    status: str
    note: str | None
    source_url: str
    source_file: str
    loaded_at: datetime


_DELETE = "delete from golden_values"
_INSERT = """
    insert into golden_values (kind, cik, accession_no, slot, metric, period_type, period_end,
                               value, prior_value, status, note, source_url, source_file)
    values (%(kind)s, %(cik)s, %(accession_no)s, %(slot)s, %(metric)s, %(period_type)s,
            %(period_end)s, %(value)s, %(prior_value)s, %(status)s, %(note)s, %(source_url)s,
            %(source_file)s)
"""
_ALL = """
    select kind, cik, accession_no, slot, metric, period_type, period_end, value, prior_value,
           status, note, source_url, source_file, loaded_at
    from golden_values
    order by kind, cik, accession_no, slot, metric, period_type, period_end
"""


async def replace_all(conn: AsyncConnection[Any], values: Sequence[GoldenValue]) -> int:
    """Store these golden values in place of those stored; how many."""
    async with conn.cursor() as cur:
        await cur.execute(_DELETE)
        await cur.executemany(
            _INSERT,
            [
                {
                    "kind": value.kind,
                    "cik": value.cik,
                    "accession_no": value.accession_no,
                    "slot": value.slot,
                    "metric": value.metric,
                    "period_type": value.period_type,
                    "period_end": value.period_end,
                    "value": value.value,
                    "prior_value": value.prior_value,
                    "status": value.status,
                    "note": value.note,
                    "source_url": value.source_url,
                    "source_file": value.source_file,
                }
                for value in values
            ],
        )
    return len(values)


async def all_values(conn: AsyncConnection[Any]) -> list[StoredGolden]:
    async with conn.cursor(row_factory=class_row(StoredGolden)) as cur:
        await cur.execute(_ALL)
        return await cur.fetchall()
