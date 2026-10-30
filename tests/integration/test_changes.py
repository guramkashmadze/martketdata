"""E5b-S2: the change feed (PLAN.md §10.2, D-027). `GET /v1/changes` serves every change to a
stored value with the old value, the new value, the reason and the value's proof now; each new
change is announced as `data.changed`, tested for each reason: a correction (an override), an
amendment (an 8-K/A) and a restatement (a later 10-Q/A)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import psycopg
import pytest
from api_contract import check_envelope
from api_seed import ACME, OLD, OLD_Q1_10Q, Q2_10Q, Q2_RELEASE, Q3_RELEASE, seed
from conftest import SEC_FIXTURES, connect_to, filing_routes, sec_serving

from marketdata.config import Settings
from marketdata.db.repositories import value_changes
from marketdata.extract.earnings_rules import Override, parse_overrides
from marketdata.ingest.earnings import EarningsIngest
from marketdata.ingest.facts import FactsLoader
from marketdata.ingest.fetch import RawFetcher
from marketdata.normalize.webhooks import DATA_CHANGED, WEBHOOK_PUBLISH
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.changes import changes_feed, get_change
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.tasks import queue
from marketdata.webhooks import outbox

Conn = psycopg.AsyncConnection[Any]

AAPL = 320193
RELEASE = "0000320193-26-000018"
AMENDMENT = "0000320193-26-000019"  # crafted from RELEASE: an 8-K/A with one value changed
EXHIBIT = "a8-kex991q3202606272026.htm"
AAPL_FACTS = (SEC_FIXTURES / "companyfacts" / "AAPL.json").read_bytes()
Q3 = ("2026-03-29", "2026-06-27")  # Apple's fiscal 2026 Q3
M = Decimal(1_000_000)
BASE = "https://data.example.com"


@pytest.fixture
async def seeded(conn: Conn) -> Conn:
    await seed(conn)
    return conn


async def test_the_feed_serves_each_change_with_its_proof_now(seeded: Conn) -> None:
    served = await changes_feed(seeded)

    amendment, restatement, correction = served.data.changes
    assert [change.reason for change in served.data.changes] == [
        "amendment",
        "restatement",
        "correction",
    ]
    assert correction.model_dump(include={"entity", "ticker", "cik", "filer_cik", "slot"}) == {
        "entity": "earnings_fact",
        "ticker": "ACME",
        "cik": ACME,
        "filer_cik": ACME,
        "slot": "revenue",
    }
    assert (correction.accession_no, correction.field) == (Q2_RELEASE, "value")
    assert (correction.old_value, correction.new_value, correction.value) == (990, 1000, 1000)
    assert correction.unit == "USD" and correction.detail == "rules version 2 to 3"
    assert correction.period_end is not None  # the release's quarter, from its 10-Q (D-097)
    assert correction.proof is not None and correction.proof.accession_no == Q2_RELEASE
    # The predecessor's metric value belongs to the company now (D-068).
    assert (restatement.ticker, restatement.cik, restatement.filer_cik) == ("ACME", ACME, OLD)
    assert (restatement.metric, restatement.period_type) == ("revenue", "Q")
    assert restatement.proof is not None
    assert restatement.proof.accession_no == OLD_Q1_10Q
    # An 8-K/A whose value now fails verification: null, with why.
    assert (amendment.accession_no, amendment.amends) == (Q3_RELEASE, Q2_RELEASE)
    assert amendment.value is None and amendment.reasons
    assert [source.accession_no for source in served.sources] == [
        Q3_RELEASE,
        Q2_10Q,
        Q2_RELEASE,
        OLD_Q1_10Q,
    ]
    assert served.as_of is not None and served.as_of >= datetime(2026, 10, 29, 12, tzinfo=UTC)


async def test_the_feed_filters_and_pages(seeded: Conn) -> None:
    by_reason = await changes_feed(seeded, reason="restatement")
    by_ticker = await changes_feed(seeded, ticker="acme")
    since = await changes_feed(seeded, since=datetime(2026, 10, 28, tzinfo=UTC))
    first = await changes_feed(seeded, limit=2)
    second = await changes_feed(seeded, limit=2, cursor=first.data.next_cursor)

    assert [change.reason for change in by_reason.data.changes] == ["restatement"]
    assert len(by_ticker.data.changes) == 3  # the predecessor's change included
    assert [change.reason for change in since.data.changes] == ["amendment", "restatement"]
    assert [change.change_id for change in first.data.changes + second.data.changes] == sorted(
        (change.change_id for change in by_ticker.data.changes), reverse=True
    )
    assert second.data.next_cursor is None
    for bad in (
        {"reason": "typo"},
        {"cursor": "x"},
        {"limit": 0},
    ):
        with pytest.raises(InvalidParameter):
            await changes_feed(seeded, **bad)  # type: ignore[arg-type]
    with pytest.raises(NotFound):
        await changes_feed(seeded, ticker="NOPE")


async def test_the_route_filters_and_answers_bad_parameters(
    api: httpx2.AsyncClient, seeded: Conn, api_key: str
) -> None:
    headers = {"X-API-Key": api_key}

    response = await api.get("/v1/changes", params={"reason": "correction"}, headers=headers)
    bad = await api.get("/v1/changes", params={"reason": "fix"}, headers=headers)

    assert response.status_code == 200
    body = response.json(parse_float=Decimal)
    check_envelope(body)
    [change] = body["data"]["changes"]
    assert (change["reason"], change["old_value"], change["new_value"]) == ("correction", 990, 1000)
    assert '"old_value":990,' in response.text
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_parameter")


def _settings(database_url: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, database_url=database_url, public_base_url=BASE
    )


async def _announced(conn: Conn) -> list[str]:
    rows = await conn.execute(
        "select subject from webhook_events where type = %s order by subject::bigint",
        [DATA_CHANGED],
    )
    return [str(row[0]) for row in await rows.fetchall()]


async def _publish_all(conn: Conn, database_url: str) -> list[dict[str, Any]]:
    """Publish every event queued and return the data.changed bodies."""
    for task in await queue.list_tasks(conn, kind=WEBHOOK_PUBLISH):
        await outbox.publish(connect_to(conn), _settings(database_url), UUID(task.key))
    rows = await conn.execute(
        "select payload::text from webhook_events where type = %s order by subject::bigint",
        [DATA_CHANGED],
    )
    return [json.loads(row[0], parse_float=Decimal) for row in await rows.fetchall()]


async def _apple(conn: Conn) -> None:
    await conn.execute(
        "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, in_universe) "
        "values (%s, 'Apple Inc.', '3571', '0926', 'domestic', true)",
        [AAPL],
    )
    await conn.execute(
        "insert into securities (cik, ticker, security_type, exchange, valid_from) "
        "values (%s, 'AAPL', 'common', 'Nasdaq', '2026-10-25')",
        [AAPL],
    )


async def _extract(
    conn: Conn,
    tmp_path: Path,
    accession: str,
    overrides: tuple[Override, ...] = (),
    routes: dict[str, bytes | int] | None = None,
) -> None:
    async with sec_serving(routes if routes is not None else filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await EarningsIngest(fetcher, connect_to(conn), overrides).run(AAPL, accession)


async def test_a_correction_is_announced_as_data_changed(
    conn: Conn, tmp_path: Path, migrated_database: str
) -> None:
    await _apple(conn)
    await _extract(conn, tmp_path, RELEASE)
    assert await _announced(conn) == []
    overrides = parse_overrides(
        '[[override]]\ncik = 320193\nslot = "operating_income"\nrow_label = "Gross margin"\n'
        'reason = "test: a pinned row"\n'
        f'source_url = "{urls.filing_document(AAPL, RELEASE, EXHIBIT)}"\n'
    )

    await _extract(conn, tmp_path, RELEASE, overrides, routes={})

    changes = await value_changes.for_accession(conn, RELEASE)
    assert await _announced(conn) == [str(change.change_id) for change in changes]
    value, prior = await _publish_all(conn, migrated_database)
    assert value["type"] == DATA_CHANGED and value["livemode"] is True
    changed = value["data"]["object"]
    assert (changed["reason"], changed["slot"], changed["field"]) == (
        "correction",
        "operating_income",
        "value",
    )
    assert (changed["old_value"], changed["new_value"]) == (35_695 * M, 54_770 * M)
    assert changed["value"] == 54_770 * M and changed["proof"]["source_quote"]
    assert prior["data"]["object"]["field"] == "prior_value"
    assert value["data"]["proof_url"] == f"{BASE}/proof/earnings/{RELEASE}"


async def test_an_amendment_is_announced_as_data_changed(
    conn: Conn, tmp_path: Path, migrated_database: str
) -> None:
    await _apple(conn)
    await _extract(conn, tmp_path, RELEASE)
    routes = filing_routes()
    page = routes[urls.filing_index_page(AAPL, RELEASE)]
    exhibit = routes[urls.filing_document(AAPL, RELEASE, EXHIBIT)]
    assert isinstance(page, bytes) and isinstance(exhibit, bytes)
    routes[urls.filing_index_page(AAPL, AMENDMENT)] = (
        page.replace(RELEASE.encode(), AMENDMENT.encode())
        .replace(RELEASE.replace("-", "").encode(), AMENDMENT.replace("-", "").encode())
        .replace(b"<strong>Form 8-K</strong>", b"<strong>Form 8-K/A</strong>")
    )
    routes[urls.filing_document(AAPL, AMENDMENT, EXHIBIT)] = exhibit.replace(
        b"29,789", b"29,790", 1
    )

    await _extract(conn, tmp_path, AMENDMENT, routes=routes)

    [change] = await value_changes.for_accession(conn, AMENDMENT)
    assert await _announced(conn) == [str(change.change_id)]
    [body] = await _publish_all(conn, migrated_database)
    changed = body["data"]["object"]
    assert (changed["reason"], changed["accession_no"], changed["amends"]) == (
        "amendment",
        AMENDMENT,
        RELEASE,
    )
    assert (changed["old_value"], changed["new_value"]) == (29_789 * M, 29_790 * M)
    assert body["data"]["proof_url"] == f"{BASE}/proof/earnings/{AMENDMENT}"


def _restated(data: bytes) -> bytes:
    """Apple's file with a later 10-Q/A that reports Q3 revenue again, 1 million lower."""
    document = json.loads(data)
    rows = document["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]
    usd = rows["units"]["USD"]
    [original] = [row for row in usd if (row.get("start"), row["end"]) == Q3 and "frame" in row]
    usd.append(
        {
            **{key: value for key, value in original.items() if key != "frame"},
            "val": original["val"] - 1_000_000,
            "accn": "0000320193-26-000099",
            "form": "10-Q/A",
            "filed": "2026-09-15",
        }
    )
    return json.dumps(document).encode()


async def test_a_restatement_is_announced_as_data_changed(
    conn: Conn, tmp_path: Path, migrated_database: str
) -> None:
    await _apple(conn)

    async def refresh(data: bytes) -> None:
        async with sec_serving({urls.companyfacts(AAPL): data}) as sec:
            fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
            await FactsLoader(fetcher, connect_to(conn)).refresh(AAPL)

    await refresh(AAPL_FACTS)
    assert await _announced(conn) == []

    await refresh(_restated(AAPL_FACTS))

    quarter, ttm = await value_changes.for_metric(conn, AAPL, "revenue")
    assert await _announced(conn) == [str(quarter.change_id), str(ttm.change_id)]
    bodies = await _publish_all(conn, migrated_database)
    changed = bodies[0]["data"]["object"]
    assert (changed["reason"], changed["metric"], changed["period_type"]) == (
        "restatement",
        "revenue",
        "Q",
    )
    assert changed["value"] == changed["new_value"] == 109_416 * M
    assert changed["proof"]["accession_no"] == "0000320193-26-000099"
    assert bodies[0]["data"]["proof_url"] == f"{BASE}/proof/metrics/AAPL/revenue/Q/2026-06-27"
    served = await get_change(conn, quarter.change_id)
    assert served.data.ticker == "AAPL" and served.sources
