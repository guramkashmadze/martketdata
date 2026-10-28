"""E4a-S4: golden reported metrics (D-084) for 5 companies x revenue, net income, diluted EPS
and total assets x the last 8 quarters, each as its earnings release prints it, built from the
committed companyfacts fixtures."""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from marketdata.normalize.companyfacts import parse_companyfacts
from marketdata.normalize.metrics import NOT_APPLICABLE_FINANCIALS, MetricRow, reported_metrics
from marketdata.sec import urls

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden" / "metrics"
FIXTURES = ROOT / "fixtures" / "sec" / "companyfacts"
METRICS = ("revenue", "net_income", "eps_diluted", "total_assets")
MILLION = Decimal(1_000_000)


def _rows(golden: dict[str, Any]) -> dict[tuple[str, Any, str], MetricRow]:
    """The company's rows and its predecessors', the company's own winning (D-084)."""
    ciks = [golden["cik"], *golden.get("predecessors", [])]
    merged: dict[tuple[str, Any, str], MetricRow] = {}
    for name in golden["fixtures"]:
        parsed = parse_companyfacts((FIXTURES / name).read_bytes())
        built = reported_metrics(
            parsed.cik,
            parsed.facts,
            fiscal_year_end=golden["fiscal_year_end"],
            sic_code=golden["sic_code"],
        )
        for row in built.rows:
            key = (row.period_type, row.period_end, row.metric)
            kept = merged.get(key)
            if kept is None or ciks.index(row.cik) < ciks.index(kept.cik):
                merged[key] = row
    return merged


def _value(metric: str, printed: str) -> Decimal:
    negative = printed.startswith("(")
    number = Decimal(printed.strip("()").replace(",", ""))
    number = number if metric == "eps_diluted" else number * MILLION
    return -number if negative else number


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.toml")), ids=lambda path: path.stem)
def test_golden_metrics(path: Path) -> None:
    golden = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = _rows(golden)
    periods = golden["period"]

    assert len([period for period in periods if period["type"] == "Q"]) == 8
    checked = 0
    for period in periods:
        assert period["url"] and all(
            (parsed := urls.parse(url)) is not None and parsed.endpoint == "filing"
            for url in period["url"]
        )
        for metric in METRICS:
            row = rows.get((period["type"], period["period_end"], metric))
            if metric in period:
                assert row is not None, (period["period_end"], metric)
                assert row.value == _value(metric, period[metric]), (period["period_end"], metric)
                assert f"{row.fiscal_year} {row.fiscal_period}" == period["fiscal"]
                checked += 1
            elif metric in period.get("not_applicable", ()):
                assert row is not None and row.value is None
                assert NOT_APPLICABLE_FINANCIALS in row.quality_flags
    assert checked >= 26  # JPMorgan, a bank, has no revenue
