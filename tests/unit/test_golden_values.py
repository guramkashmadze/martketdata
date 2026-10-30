"""E5b-S4: the golden sets as values for the accuracy report, read as the golden tests read them,
and the latency percentiles it publishes."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.normalize.golden import GoldenFileError, earnings_values, metric_values
from marketdata.service.accuracy import latency, rate

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
M = Decimal(1_000_000)


def test_a_release_gives_its_four_slots_scaled() -> None:
    values = earnings_values((GOLDEN / "earnings" / "aapl.toml").read_text(), "golden/aapl")

    revenue = next(
        value
        for value in values
        if value.accession_no == "0000320193-25-000077" and value.slot == "revenue"
    )
    eps = next(
        value
        for value in values
        if value.accession_no == "0000320193-25-000077" and value.slot == "eps_diluted"
    )
    assert (revenue.value, revenue.prior_value) == (102_466 * M, 94_930 * M)
    assert (eps.value, eps.prior_value) == (Decimal("1.85"), Decimal("0.97"))  # not scaled
    assert revenue.status == "expected" and revenue.cik == 320193
    assert revenue.source_url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert len(values) == 4 * 4  # four releases, four slots each


def test_differences_and_slots_that_dont_apply() -> None:
    micron = earnings_values((GOLDEN / "earnings" / "mu.toml").read_text(), "golden/mu")
    bank = earnings_values((GOLDEN / "earnings" / "jpm.toml").read_text(), "golden/jpm")

    over_limit = [value for value in micron if value.status == "known_difference"]
    assert {value.slot for value in over_limit} == {
        "operating_income",
        "net_income",
        "eps_diluted",
    }
    assert over_limit[0].note is not None and "1000%" in over_limit[0].note
    assert over_limit[0].value == 43_751 * M  # the printed value, still compared
    revenue = [value for value in bank if value.slot == "revenue"]
    assert revenue and all(value.status == "not_applicable" for value in revenue)
    assert all(value.value is None for value in revenue)


def test_a_release_without_a_statement_has_no_values() -> None:
    every = [
        value
        for path in sorted((GOLDEN / "earnings").glob("*.toml"))
        for value in earnings_values(path.read_text(), path.name)
    ]

    accessions = {value.accession_no for value in every}
    assert len(accessions) >= 38
    # NextEra's and Target's 8-Ks with item 2.02 that hold no results (D-071).
    assert not accessions & {"0000753308-26-000004", "0000027419-26-000009"}


def test_metric_periods_give_values_in_base_units() -> None:
    apple = metric_values((GOLDEN / "metrics" / "aapl.toml").read_text(), "golden/metrics/aapl")
    bank = metric_values((GOLDEN / "metrics" / "jpm.toml").read_text(), "golden/metrics/jpm")

    total_assets = next(
        value
        for value in apple
        if value.metric == "total_assets" and value.period_end == date(2024, 9, 28)
    )
    assert (total_assets.period_type, total_assets.value) == ("Q", 364_980 * M)
    assert total_assets.kind == "metric" and total_assets.accession_no is None
    debt = [value for value in bank if value.metric == "long_term_debt"]
    assert debt and all(value.status == "known_difference" for value in debt)
    assert all(value.note and "not in the catalog" in value.note for value in debt)
    assert [value.status for value in bank if value.metric == "revenue"] == [
        "not_applicable"
    ] * len([value for value in bank if value.metric == "revenue"])


def test_every_golden_file_is_read() -> None:
    earnings = [
        value
        for path in sorted((GOLDEN / "earnings").glob("*.toml"))
        for value in earnings_values(path.read_text(), path.name)
    ]
    metrics = [
        value
        for path in sorted((GOLDEN / "metrics").glob("*.toml"))
        for value in metric_values(path.read_text(), path.name)
    ]

    assert len(earnings) >= 150 and len(metrics) >= 650
    assert sum(value.status == "known_difference" for value in metrics) == 26  # D-093


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("cik = 1\n[[release]]\naccession_no = 'a'\nurl = 'u'\nstatus = 'complete'\n", "has no"),
        ("not toml", "golden/bad"),
        (
            "cik = 1\n[[release]]\naccession_no = 'a'\nurl = 'u'\nstatus = 'complete'\n"
            "revenue = ['1', '2']\noperating_income = ['1', '2']\nnet_income = ['1', '2']\n"
            "eps_diluted = ['1', '2']\n",
            "scale must be",
        ),
    ],
)
def test_a_broken_file_is_named(text: str, error: str) -> None:
    with pytest.raises(GoldenFileError, match=error):
        earnings_values(text, "golden/bad")


def test_latency_is_published_as_median_and_90th_percentile_minutes() -> None:
    minutes = [Decimal(seconds) for seconds in (120, 240, 300, 600, 3600)]

    measured = latency(minutes)
    nothing = latency([])

    assert measured.releases == 5
    assert measured.median_minutes == Decimal("5.0")  # 300 s
    assert measured.p90_minutes == Decimal("40.0")  # 600 + 0.6 x 3000 s, as percentile_cont
    assert (nothing.releases, nothing.median_minutes, nothing.p90_minutes) == (0, None, None)
    assert rate(2, 3).rate == Decimal("66.7") and rate(0, 0).rate is None
