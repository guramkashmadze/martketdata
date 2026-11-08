"""Response envelope from PLAN.md §10.1 (E5a-S1).

Every successful /v1 response is `{"data", "sources", "as_of", "quality_flags", "disclaimer"}`;
one about a company, a comparison or a 13F manager adds `checked_at` and `check_overdue` after
`as_of` (D-135). Numbers are JSON numbers written from the stored Decimal digit for digit, never
through a float (D-096; service/json_text.py); times are UTC with a `Z`, dates `YYYY-MM-DD`.
"""

from typing import Any

from fastapi.responses import JSONResponse

from marketdata.service.json_text import dumps
from marketdata.service.served import DISCLAIMER, Served, checked_fields

__all__ = ["DISCLAIMER", "Envelope", "EnvelopeResponse", "dumps", "respond"]


class Envelope[T](Served[T]):
    disclaimer: str = DISCLAIMER


class EnvelopeResponse(JSONResponse):
    """JSON whose numbers keep every digit of their Decimal."""

    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")


def respond(served: Served[Any]) -> EnvelopeResponse:
    written = checked_fields(served.model_dump(mode="python"))
    return EnvelopeResponse({**written, "disclaimer": DISCLAIMER})
