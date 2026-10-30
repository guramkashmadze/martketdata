"""JSON text of served data (D-096): numbers written from the stored Decimal digit for digit,
never through a float; times in UTC with a `Z`, dates `YYYY-MM-DD`. The API's envelope
(api/envelope.py) and webhook bodies (webhooks/outbox.py) are written with it."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


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
            return json.dumps(utc_text(value))
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


def utc_text(moment: datetime) -> str:
    """`2026-10-30T20:31:05Z`: a time in UTC as the API writes it."""
    if moment.tzinfo is None:
        raise ValueError(f"{moment} has no time zone")
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError(f"{value} is not a JSON number")
    return format(value, "f")
