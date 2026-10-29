"""Parameters the /v1 routes share (PLAN.md §10.1): tickers normalized as §9.2 says, lists a
page at a time, comma-separated lists, and days or times."""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import Path, Query

from marketdata.service.errors import InvalidParameter
from marketdata.service.pages import MAX_LIMIT

TickerPath = Annotated[
    str,
    Path(
        description=(
            "A ticker as people write it: case doesn't matter and `.` or `-` separate a share "
            "class (`brk.b` and `BRK-B` are the same; in a path, write `BRK-B` rather than "
            "`BRK/B`)."
        ),
        examples=["AAPL"],
    ),
]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Items per page, 1 to 100.")]
Cursor = Annotated[
    str | None,
    Query(description="The `next_cursor` of the previous page, with the same other parameters."),
]
PeriodType = Literal["Q", "FY", "TTM"]


def names(text: str | None) -> list[str] | None:
    """`a, b,c` -> ['a', 'b', 'c']; nothing given -> None."""
    if text is None:
        return None
    listed = [part.strip() for part in text.split(",") if part.strip()]
    return listed or None


def moment(text: str | None, name: str) -> date | datetime | None:
    """A day (`2026-10-01`, from its start in UTC) or a time (`2026-10-01T20:30:00Z`)."""
    if text is None:
        return None
    try:
        return date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text)
    except ValueError:
        raise InvalidParameter(
            f"{name} must be a day like 2026-10-01 or a time like 2026-10-01T20:30:00Z, "
            f"not {text!r}"
        ) from None
