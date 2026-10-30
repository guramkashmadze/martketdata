"""Proof pages rendered server-side with Jinja2 (E5b-S3, PLAN.md §9.10).

Templates are autoescaped; a page loads no script, font or image and sets no cookie (§9.14:
no trackers, no third-party requests), which its Content-Security-Policy enforces. Pages sit
behind a key, so neither a browser nor a proxy keeps them. Numbers are shown with every digit of
the stored value and their unit, never rounded.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from marketdata.sec import urls
from marketdata.service.proof import CHECKS, CROSS_CHECKS, METHODS, REASONS, SLOT_NAMES
from marketdata.service.served import DISCLAIMER

HEADERS = {
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
UNITS = {"USD": "USD", "USD/shares": "USD per share", "shares": "shares"}
PERIOD_NAMES = {"Q": "Quarter", "FY": "Fiscal year", "TTM": "Twelve months"}
TITLES = {
    400: "This address isn't right",
    401: "Sign in with your API key",
    403: "This key can't open proof pages",
    404: "Not found",
    405: "Not allowed",
    422: "Not applicable",
    429: "Too many requests",
    500: "Something went wrong",
}


def amount(value: Decimal | int | None, unit: str | None) -> str:
    """`109,417,000,000 USD`, `2.02 USD per share`, `16.4%`; a missing value is a dash."""
    if value is None:
        return "—"
    text = f"{value:,}"
    if unit == "percent":
        return f"{text}%"
    return f"{text} {UNITS.get(unit or '', unit or '')}".rstrip()


def utc(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


_environment = Environment(
    loader=PackageLoader("marketdata.api.proof_pages", "templates"),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_environment.filters.update(amount=amount, utc=utc)
_environment.globals.update(
    disclaimer=DISCLAIMER,
    filing_url=urls.filing_index_page,
    slot_names=SLOT_NAMES,
    checks=CHECKS,
    cross_checks=CROSS_CHECKS,
    methods=METHODS,
    reasons=REASONS,
    period_names=PERIOD_NAMES,
)


def page(
    template: str,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    **context: Any,
) -> HTMLResponse:
    html = _environment.get_template(template).render(**context)
    return HTMLResponse(html, status_code=status, headers={**HEADERS, **(headers or {})})


def error_page(status: int, message: str, headers: Mapping[str, str] | None = None) -> HTMLResponse:
    return page(
        "error.html",
        status=status,
        headers=headers,
        code=status,
        title=TITLES.get(status, "Error"),
        message=message,
    )
