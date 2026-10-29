"""Response envelope from PLAN.md §10.1 (E5a-S1).

Every successful /v1 response is `{"data", "sources", "as_of", "quality_flags", "disclaimer"}`.
Numbers are JSON numbers written from the stored Decimal digit for digit, never through a float
(D-096); times are UTC with a `Z`, dates `YYYY-MM-DD`.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi.responses import JSONResponse

from marketdata.service.served import Served

DISCLAIMER = "Information from public filings. Not investment advice."


class Envelope[T](Served[T]):
    disclaimer: str = DISCLAIMER


class EnvelopeResponse(JSONResponse):
    """JSON whose numbers keep every digit of their Decimal."""

    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")


def respond(served: Served[Any]) -> EnvelopeResponse:
    return EnvelopeResponse({**served.model_dump(mode="python"), "disclaimer": DISCLAIMER})


def dumps(value: Any) -> str:
    """JSON text of what `model_dump(mode="python")` gives: None, bool, int, Decimal, str, date,
    datetime, lists, tuples and dicts. Floats are refused: no served value is one."""
    match value:
        case None:
            return "null"
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case Decimal():
            return _number(value)
        case str():
            return json.dumps(value, ensure_ascii=False)
        case datetime():
            return json.dumps(_utc(value))
        case date():
            return json.dumps(value.isoformat())
        case dict():
            items = (
                f"{json.dumps(str(k), ensure_ascii=False)}:{dumps(v)}" for k, v in value.items()
            )
            return "{" + ",".join(items) + "}"
        case list() | tuple():
            return "[" + ",".join(dumps(item) for item in value) + "]"
    raise TypeError(f"{type(value).__name__} values are not written to JSON")


def _number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError(f"{value} is not a JSON number")
    return format(value, "f")


def _utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError(f"{moment} has no time zone")
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
