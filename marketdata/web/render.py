"""Pages rendered server-side with Jinja2 (E15, PLAN.md §9.14): the web UI's pages and the proof
pages (§9.10), in one layout with the disclaimer on every page.

Templates are autoescaped. Every page sends a Content-Security-Policy that lets it load scripts,
styles and images from this site only, and no inline script: our static files and the two
vendored libraries are all a page loads, so the browser makes no third-party request. A page
sets no cookie, and neither a browser nor a proxy keeps it.

Numbers are never rounded. Proof pages show every digit of a stored value with its unit
(`amount`); tables elsewhere state a scale, USD millions or millions of shares, and show each
value exactly in it (`scaled`: 391,035, or 12,345.678 for a value in thousands).
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from marketdata.sec import urls
from marketdata.service.accuracy import Rate
from marketdata.service.proof import CHECKS, CROSS_CHECKS, METHODS, REASONS, SLOT_NAMES
from marketdata.service.served import DISCLAIMER
from marketdata.web.assets import CHART_JS, HTMX, asset_url

HEADERS = {
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
UNITS = {"USD": "USD", "USD/shares": "USD per share", "shares": "shares"}
# The scale a table shows a unit in, and the scale's name.
SCALES: dict[str, tuple[Decimal, str]] = {
    "USD": (Decimal(1_000_000), "USD millions"),
    "shares": (Decimal(1_000_000), "millions of shares"),
    "USD/shares": (Decimal(1), "USD per share"),
    "percent": (Decimal(1), "%"),
}
PERIOD_NAMES = {"Q": "Quarter", "FY": "Fiscal year", "TTM": "Twelve months"}
# The metric catalog's metrics (PLAN.md §9.3) as pages name them.
METRIC_NAMES = {
    "revenue": "Revenue",
    "cost_of_revenue": "Cost of revenue",
    "gross_profit": "Gross profit",
    "rnd_expense": "Research and development",
    "sga_expense": "Selling, general and administrative",
    "operating_income": "Operating income",
    "interest_expense": "Interest expense",
    "pretax_income": "Income before taxes",
    "income_tax": "Income tax",
    "net_income": "Net income",
    "eps_basic": "EPS, basic",
    "eps_diluted": "EPS, diluted",
    "shares_diluted_avg": "Diluted shares, average",
    "depreciation_amortization": "Depreciation and amortization",
    "operating_cash_flow": "Operating cash flow",
    "capex": "Capital expenditure",
    "dividends_paid": "Dividends paid",
    "buybacks": "Share buybacks",
    "cash": "Cash",
    "current_assets": "Current assets",
    "total_assets": "Total assets",
    "current_liabilities": "Current liabilities",
    "total_liabilities": "Total liabilities",
    "long_term_debt": "Long-term debt",
    "total_equity": "Total equity",
    "shares_outstanding": "Shares outstanding",
    "free_cash_flow": "Free cash flow",
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "revenue_growth_yoy": "Revenue growth, year over year",
    "eps_diluted_growth_yoy": "Diluted EPS growth, year over year",
}
TITLES = {
    400: "This address isn't right",
    401: "Sign in with your API key",
    403: "This key can't open these pages",
    404: "Not found",
    405: "Not allowed",
    422: "Not applicable",
    429: "Too many requests",
    500: "Something went wrong",
}


def exact(value: Decimal | int | None) -> str:
    """Every digit of a value with thousands separators and no trailing zeros: 1,234.5; a
    missing value is a dash."""
    if value is None:
        return "—"
    number = Decimal(value).normalize()
    return f"{number:,f}"


def amount(value: Decimal | int | None, unit: str | None) -> str:
    """`109,417,000,000 USD`, `2.02 USD per share`, `16.4%`; a missing value is a dash."""
    if value is None:
        return "—"
    text = f"{value:,}"
    if unit == "percent":
        return f"{text}%"
    return f"{text} {UNITS.get(unit or '', unit or '')}".rstrip()


def scaled(value: Decimal | int | None, unit: str) -> str:
    """A value exactly in its table's scale (`SCALES`): 391,035,000,000 USD is 391,035; a
    percentage keeps its sign `%`."""
    if value is None:
        return "—"
    divisor, _ = SCALES.get(unit, (Decimal(1), unit))
    text = exact(Decimal(value) / divisor)
    return f"{text}%" if unit == "percent" else text


def scale_name(unit: str) -> str:
    return SCALES.get(unit, (Decimal(1), UNITS.get(unit, unit)))[1]


def change(value: Decimal | int | None) -> str:
    """A change in percent with its sign: +5.3%, -2.1%, 0%; missing, a dash."""
    if value is None:
        return "—"
    text = exact(value)
    return f"+{text}%" if Decimal(value) > 0 else f"{text}%"


def direction(value: Decimal | int | None) -> str:
    """The CSS class of a change: up, down or none (conventional colours, never a verdict)."""
    if value is None or Decimal(value) == 0:
        return ""
    return "up" if Decimal(value) > 0 else "down"


def pct(value: Decimal | None) -> str:
    """A rate in percent, 99.7%; nothing counted, a dash."""
    return "—" if value is None else f"{exact(value)}%"


def rate_text(counted: Rate) -> str:
    """`66.7% (2 of 3)`, or a dash while nothing is counted."""
    if counted.rate is None:
        return "— (nothing checked yet)"
    return f"{exact(counted.rate)}% ({counted.matched:,} of {counted.checked:,})"


def utc(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def day(moment: date | datetime | None) -> str:
    if moment is None:
        return "—"
    if isinstance(moment, datetime):
        return moment.astimezone(UTC).date().isoformat()
    return moment.isoformat()


_environment = Environment(
    loader=PackageLoader("marketdata.web", "templates"),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_environment.filters.update(
    amount=amount,
    utc=utc,
    day=day,
    exact=exact,
    scaled=scaled,
    change=change,
    direction=direction,
)
_environment.globals.update(
    disclaimer=DISCLAIMER,
    asset=asset_url,
    htmx=asset_url(HTMX),
    chart_js=asset_url(CHART_JS),
    filing_url=urls.filing_index_page,
    scale_name=scale_name,
    pct=pct,
    rate_text=rate_text,
    metric_names=METRIC_NAMES,
    slot_names=SLOT_NAMES,
    checks=CHECKS,
    cross_checks=CROSS_CHECKS,
    methods=METHODS,
    reasons=REASONS,
    period_names=PERIOD_NAMES,
)


def render(template: str, **context: Any) -> str:
    return _environment.get_template(template).render(**context)


def page(
    template: str,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    **context: Any,
) -> HTMLResponse:
    return HTMLResponse(
        render(template, **context), status_code=status, headers={**HEADERS, **(headers or {})}
    )


def error_page(status: int, message: str, headers: Mapping[str, str] | None = None) -> HTMLResponse:
    return page(
        "error.html",
        status=status,
        headers=headers,
        code=status,
        title=TITLES.get(status, "Error"),
        message=message,
    )
