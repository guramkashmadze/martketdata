"""Golden metrics (E4a-S4, E4b-S1, E4b-S4; D-084): each value as a company's earnings release
prints it, or the difference of two printed year-to-date columns for a derived quarter, read by
hand and compared with the rows built from the committed companyfacts fixtures."""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from marketdata.normalize.companyfacts import parse_companyfacts
from marketdata.normalize.financials import build_metrics
from marketdata.normalize.metric_catalog import UNITS
from marketdata.normalize.metrics import NOT_APPLICABLE_FINANCIALS, MetricRow
from marketdata.sec import urls

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden" / "metrics"
FIXTURES = ROOT / "fixtures" / "sec" / "companyfacts"
PERIOD_KEYS = {"type", "period_end", "fiscal", "url", "not_applicable", "known_differences"}
MILLION = Decimal(1_000_000)


def _rows(golden: dict[str, Any]) -> dict[tuple[str, Any, str], MetricRow]:
    """The company's rows, its predecessors' facts helping derive its quarters, then each
    predecessor's own rows where the company has none (D-084), as ingest/facts.py stores and
    the service layer merges them."""
    parsed = [parse_companyfacts((FIXTURES / name).read_bytes()) for name in golden["fixtures"]]
    company, *predecessors = parsed
    merged: dict[tuple[str, Any, str], MetricRow] = {}
    builds = [
        (company, [fact for earlier in predecessors for fact in earlier.facts]),
        *((earlier, []) for earlier in predecessors),
    ]
    for filer, context in builds:
        built = build_metrics(
            filer.cik,
            filer.facts,
            fiscal_year_end=golden["fiscal_year_end"],
            sic_code=golden["sic_code"],
            predecessor_facts=context,
        )
        for row in built.rows:
            merged.setdefault((row.period_type, row.period_end, row.metric), row)
    return merged


def _value(metric: str, printed: str) -> Decimal:
    """Money and share counts are given in millions; per-share values and percentages as
    printed."""
    negative = printed.startswith("(")
    number = Decimal(printed.strip("()").replace(",", ""))
    if UNITS[metric] in ("USD", "shares"):
        number *= MILLION
    return -number if negative else number


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.toml")), ids=lambda path: path.stem)
def test_golden_metrics(path: Path) -> None:
    golden = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = _rows(golden)
    periods = golden["period"]

    assert len([period for period in periods if period["type"] == "Q"]) >= 7
    checked = 0
    for period in periods:
        assert period["url"] and all(
            (parsed := urls.parse(url)) is not None and parsed.endpoint == "filing"
            for url in period["url"]
        )
        known = period.get("known_differences", {})
        for metric in set(period) - PERIOD_KEYS:
            row = rows.get((period["type"], period["period_end"], metric))
            assert row is not None, (period["period_end"], metric)
            assert f"{row.fiscal_year} {row.fiscal_period}" == period["fiscal"]
            if metric in known:
                # Listed in PLAN.md §16: the release and the filing differ; still true?
                assert row.value != _value(metric, period[metric]), (period["period_end"], metric)
                continue
            assert row.value == _value(metric, period[metric]), (period["period_end"], metric)
            checked += 1
        for metric in period.get("not_applicable", ()):
            row = rows[(period["type"], period["period_end"], metric)]
            assert row.value is None
            assert NOT_APPLICABLE_FINANCIALS in row.quality_flags
    assert checked >= 14
