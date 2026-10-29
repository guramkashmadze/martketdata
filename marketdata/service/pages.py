"""Lists a page at a time (PLAN.md §10.1): `limit` (default 20, at most 100) and an opaque
`cursor` that the previous page returned as `next_cursor`.

A cursor is the sort key of the last item served, as URL-safe base64 of a JSON list, so the
next page starts after it even when new items arrive meanwhile (D-098).
"""

import base64
import json

from marketdata.service.errors import InvalidParameter

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


BAD_CURSOR = (
    "cursor is not one this endpoint returned; pass the previous page's next_cursor as it is, "
    "with the same parameters"
)


def check_limit(limit: int, maximum: int = MAX_LIMIT) -> None:
    if not 1 <= limit <= maximum:
        raise InvalidParameter(f"limit must be from 1 to {maximum}, got {limit}")


def encode_cursor(*parts: str) -> str:
    text = json.dumps(list(parts), separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, size: int) -> list[str]:
    """The `size` parts of a cursor this API returned; InvalidParameter for anything else."""
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        parts = json.loads(text)
    except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors too
        parts = None
    if (
        not isinstance(parts, list)
        or len(parts) != size
        or not all(isinstance(part, str) for part in parts)
    ):
        raise InvalidParameter(BAD_CURSOR)
    return parts
