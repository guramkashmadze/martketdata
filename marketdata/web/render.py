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

import calendar
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from marketdata.sec import urls
from marketdata.service.accuracy import Rate
from marketdata.service.financials import MetricView
from marketdata.service.funds import FundPosition
from marketdata.service.insiders import Insider, InsiderTrade
from marketdata.service.proof import (
    CHECKS,
    CROSS_CHECKS,
    INSIDER_CHECKS,
    INSIDER_FLAGS,
    METHODS,
    REASONS,
    SLOT_NAMES,
    SUPERSEDED,
)
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
CLASS_NAMES = {
    "domestic": "US domestic filer",
    "foreign_private_issuer": "Foreign private issuer",
    "fund": "Fund",
    "other": "Other filer",
    "unknown": "Not classified yet",
}
CLASS_GROUPS = {
    "domestic": "US domestic filers",
    "foreign_private_issuer": "Foreign private issuers",
    "fund": "Funds",
    "other": "Other filers",
    "unknown": "Not classified yet",
}
KIND_NAMES = {
    "earnings": "Earnings releases",
    "fundamentals": "Financial metrics from the 10-K and 10-Q",
    "insiders": "Insider trades (Form 4)",
}
ROLE_NAMES = {
    "director": "Director",
    "officer": "Officer",
    "ten_percent_owner": "10% owner",
    "other": "Other",
}
CHANGE_NAMES = {
    "new": "New",
    "added": "Added",
    "trimmed": "Trimmed",
    "unchanged": "Unchanged",
    "exited": "Exited",
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


def signed(value: Decimal | int | None) -> str:
    """Every digit with its sign: +1,000, -500; missing, a dash."""
    if value is None:
        return "—"
    text = exact(value)
    return f"+{text}" if Decimal(value) > 0 else text


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


def company_url(ticker: str) -> str:
    return f"/companies/{quote(ticker, safe='')}"


def metric_url(ticker: str, row: MetricView) -> str:
    """The proof page of a metric value, under the company's ticker."""
    return (
        f"/proof/metrics/{quote(ticker, safe='')}/{row.metric}/{row.period_type}/"
        f"{row.period_end.isoformat()}"
    )


def release_url(accession_no: str, slot: str | None = None) -> str:
    """A release's proof page, at one of its values when a slot is named."""
    return f"/proof/earnings/{accession_no}" + (f"#{slot}" if slot else "")


def insider_url(accession_no: str, line: int | None = None) -> str:
    """A Form 4's proof page, at one of its lines when one is named."""
    return f"/proof/insiders/{accession_no}" + (f"#line-{line}" if line else "")


def trade_url(trade: InsiderTrade) -> str:
    """An insider trade's proof: the first filing it sums, at its first line."""
    first = trade.proof.filings[0]
    return insider_url(first.accession_no, first.lines[0] if first.lines else None)


def position_url(manager_cik: int, quarter: str, position: FundPosition) -> str:
    """A fund position's proof page."""
    url = f"/proof/funds/{manager_cik}/{quarter}/{quote(position.cusip, safe='')}"
    return f"{url}?put_call={position.put_call}" if position.put_call else url


def insider_names(insiders: Sequence[Insider]) -> str:
    """The insiders of one filing: joint filers are one group."""
    return " and ".join(insider.name for insider in insiders) or "—"


def insider_roles(insider: Insider) -> str:
    """`Director; Officer (Chief Executive Officer)` as the filing ticks them."""
    roles = []
    for role in insider.roles:
        name = ROLE_NAMES.get(role, role)
        if role == "officer" and insider.officer_title:
            name = f"{name} ({insider.officer_title})"
        if role == "other" and insider.other_text:
            name = f"{name} ({insider.other_text})"
        roles.append(name)
    return "; ".join(roles)


def year_end(mmdd: str | None) -> str:
    """A fiscal year end as the SEC gives it, '0927', as September 27."""
    if not mmdd or len(mmdd) != 4 or not mmdd.isdigit() or not 1 <= int(mmdd[:2]) <= 12:
        return "—"
    return f"{calendar.month_name[int(mmdd[:2])]} {int(mmdd[2:])}"


def yes_no(box: bool | None) -> str:
    return "—" if box is None else ("Yes" if box else "No")


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
    signed=signed,
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
    company_url=company_url,
    metric_url=metric_url,
    release_url=release_url,
    insider_url=insider_url,
    trade_url=trade_url,
    position_url=position_url,
    insider_names=insider_names,
    insider_roles=insider_roles,
    year_end=year_end,
    yes_no=yes_no,
    metric_names=METRIC_NAMES,
    slot_names=SLOT_NAMES,
    class_names=CLASS_NAMES,
    class_groups=CLASS_GROUPS,
    kind_names=KIND_NAMES,
    change_names=CHANGE_NAMES,
    checks=CHECKS,
    cross_checks=CROSS_CHECKS,
    insider_checks=INSIDER_CHECKS,
    insider_flags=INSIDER_FLAGS,
    superseded=SUPERSEDED,
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
