"""E15-S4: the status page (PLAN.md §9.14, §9.10): freshness, the track record and the accuracy
report, the same report /v1/status serves, reused for a minute."""

from datetime import date
from decimal import Decimal
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import ACME, OLD_Q1_RELEASE, Q2_RELEASE, seed
from web_pages import DISCLAIMER, basic

from marketdata.db.repositories import golden
from marketdata.normalize.golden import GoldenValue

Conn = psycopg.AsyncConnection[Any]
URL = "https://www.sec.gov/Archives/edgar/data/1001/000100100026000020/ex99.htm"


def _golden(slot: str | None, metric: str | None, value: str, prior: str | None) -> GoldenValue:
    return GoldenValue(
        kind="earnings" if slot else "metric",
        cik=ACME,
        accession_no=Q2_RELEASE if slot else None,
        slot=slot,
        metric=metric,
        period_type=None if slot else "Q",
        period_end=None if slot else date(2026, 6, 30),
        value=Decimal(value),
        prior_value=Decimal(prior) if prior is not None else None,
        status="expected",
        note=None,
        source_url=URL,
        source_file="golden/acme.toml",
    )


@pytest.fixture
async def auth(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    await golden.replace_all(
        conn,
        [
            _golden("revenue", None, "1000", "950"),  # served exactly
            _golden("eps_diluted", None, "1.30", "1.10"),  # 1.25 served: a miss
            _golden(None, "revenue", "1000", None),  # served exactly
        ],
    )
    return basic(api_key)


async def test_the_status_page_shows_freshness_and_the_accuracy_report(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    response = await api.get("/status", headers=auth)

    assert response.status_code == 200, response.text
    page = response.text
    assert "Freshness and accuracy" in page and DISCLAIMER in page
    assert "accepted by the SEC 2026-10-28 20:05:00 UTC" in page
    assert "50% (1 of 2)" in page  # the release values served exactly
    assert "100% (1 of 1)" in page  # the metric values
    # The cross-check: one release value matched its 10-Q, one didn't, and the mismatch is
    # listed with both values and a link to its proof.
    assert '<span class="value num">50%</span>' in page
    assert f'href="/proof/earnings/{Q2_RELEASE}#' in page
    assert "No earnings release has been processed as it was filed yet" in page
    assert 'aria-current="page"' in page and "<script" not in page


async def test_the_track_record_counts_only_releases_processed_live(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    for accession, minutes in ((Q2_RELEASE, 4), (OLD_Q1_RELEASE, 10)):
        await conn.execute(
            "update earnings_releases set live = true, "
            "verified_at = accepted_at + %s * interval '1 minute' where accession_no = %s",
            [minutes, accession],
        )

    page = (await api.get("/status", headers=auth)).text

    assert "2 earnings releases processed as they were filed" in page
    assert '<span class="num">7</span>' in page  # the median, in minutes


async def test_the_page_and_the_api_reuse_one_report_a_minute(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    first = await api.get("/v1/status")
    await golden.replace_all(conn, [])  # the report changes, but is reused
    page = (await api.get("/status", headers=auth)).text

    assert first.status_code == 200
    assert "50% (1 of 2)" in page
