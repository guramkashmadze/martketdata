"""What every data service function returns (PLAN.md §10.1): the data, the filings it came from,
how fresh it is and the quality flags on its values. The REST API wraps it in the envelope with
the disclaimer (api/envelope.py); the MCP server (E6) returns the same content.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class Source(BaseModel):
    """A filing a served value came from."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str | None
    filed_at: date | None
    url: str  # the filing's folder on sec.gov


class FlagRef(BaseModel):
    """A quality flag (§9.5) on one served value."""

    model_config = ConfigDict(frozen=True)

    code: str
    metric: str  # the metric, or the earnings slot
    period_end: date | None


class Served[T](BaseModel):
    model_config = ConfigDict(frozen=True)

    data: T
    sources: tuple[Source, ...]
    as_of: datetime | None  # UTC: when the newest row used was last written
    quality_flags: tuple[FlagRef, ...]
