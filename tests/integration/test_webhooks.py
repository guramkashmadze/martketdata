"""E5b-S1: webhooks (PLAN.md §9.11).

Events are written where the data changes: a release the watcher routed is verified, a facts
refresh adds or changes rows, a watch finds a filing with a high-importance label. Publishing
builds the §9.11 envelope around the API's own object and queues a delivery to each subscribed
destination. A delivery is signed (the reference receiver checks it), goes only to a public
address, is tried three times about 1 and 10 minutes apart, and 50 failed deliveries in a row
switch the destination off.
"""

import importlib.util
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import httpx2
import psycopg
import pytest
from api_seed import ACME, Q2_RELEASE, Q3_RELEASE, seed
from conftest import SEC_FIXTURES, LoadMaster, connect_to, filing_routes, sec_serving
from cryptography.fernet import Fernet
from structlog.testing import capture_logs

from marketdata.config import Settings
from marketdata.ingest.announce import announce
from marketdata.ingest.earnings import EarningsIngest
from marketdata.ingest.facts import FactsLoader, rebuild_all
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.watcher import FilingsWatcher
from marketdata.normalize.filings import EARNINGS_EXTRACT
from marketdata.normalize.webhooks import (
    EARNINGS_VERIFIED,
    FILING_EVENT,
    FINANCIALS_UPDATED,
    WEBHOOK_DELIVER,
    WEBHOOK_PUBLISH,
)
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service import webhooks as webhook_service
from marketdata.service.clients import create_client
from marketdata.service.earnings import get_earnings
from marketdata.service.errors import InvalidParameter
from marketdata.service.json_text import dumps
from marketdata.tasks import queue
from marketdata.tasks.handlers import DELIVERY_RETRY, delivery_handler
from marketdata.tasks.registry import TaskContext, TaskRefused
from marketdata.webhooks import deliver, outbox
from marketdata.webhooks.deliver import DeliveryFailed, DeliveryRefused
from marketdata.webhooks.signing import encrypt_secret, new_secret

Conn = psycopg.AsyncConnection[Any]

ROOT = Path(__file__).resolve().parents[2]
KEY = Fernet.generate_key().decode()
BASE = "https://data.example.com"
PUBLIC_IP = "93.184.215.14"
AAPL = 320193
AAPL_RELEASE = "0000320193-26-000018"  # Apple's Q3 2026 release, in the fixtures
AAPL_OLDER = "0000320193-26-000011"
AAPL_FACTS = (SEC_FIXTURES / "companyfacts" / "AAPL.json").read_bytes()
AAPL_SUBMISSIONS = (SEC_FIXTURES / "submissions" / "AAPL-20261025.json").read_bytes()


def _load_receiver() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "webhook_receiver", ROOT / "scripts" / "webhook_receiver.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


receiver = _load_receiver()


class Receiver:
    """A client's endpoint: it checks each delivery's signature as the reference receiver does
    and answers `status`."""

    def __init__(self, secret: str, status: int = 200, headers: dict[str, str] | None = None):
        self.secret = secret
        self.status = status
        self.headers = headers or {}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        receiver.verify(self.secret, request.headers.get("X-Signature"), request.content)
        return httpx2.Response(self.status, headers=self.headers)


async def _public(host: str, port: int) -> list[str]:  # noqa: ASYNC124 - a resolver
    return [PUBLIC_IP]


def _settings(database_url: str, env: str = "live") -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        env=env,
        webhook_encryption_key=KEY,
        public_base_url=BASE,
    )


async def _destination(
    conn: Conn,
    url: str = "https://hooks.example.com/in",
    types: tuple[str, ...] = (EARNINGS_VERIFIED,),
    *,
    allow_loopback: bool = False,
) -> tuple[int, str]:
    await create_client(conn, "test client")
    secret = new_secret()
    destination = await webhook_service.create_destination(
        conn,
        "test client",
        url,
        types,
        secret_enc=encrypt_secret(KEY, secret),
        allow_loopback=allow_loopback,
    )
    return destination.dest_id, secret


async def _events(conn: Conn, event_type: str) -> list[tuple[str, dict[str, Any], bool]]:
    rows = await conn.execute(
        "select subject, detail, livemode from webhook_events where type = %s "
        "order by created_at, subject",
        [event_type],
    )
    return [(subject, detail, livemode) for subject, detail, livemode in await rows.fetchall()]


async def _payload(conn: Conn, event_id: UUID) -> str:
    row = await (
        await conn.execute(
            "select payload::text from webhook_events where event_id = %s", [event_id]
        )
    ).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


async def _published(conn: Conn, database_url: str) -> UUID:
    """ACME's second-quarter release announced and published."""
    event_id = await announce(conn, EARNINGS_VERIFIED, Q2_RELEASE)
    assert event_id is not None
    await outbox.publish(connect_to(conn), _settings(database_url), event_id)
    return event_id


@pytest.fixture
async def context(conn: Conn, migrated_database: str, tmp_path: Path) -> AsyncIterator[TaskContext]:
    async with sec_serving({}) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        yield TaskContext(_settings(migrated_database), connect_to(conn), fetcher, frozenset())


async def test_publishing_builds_the_envelope_and_queues_a_delivery_per_subscriber(
    conn: Conn, migrated_database: str
) -> None:
    await seed(conn)
    wanted, _ = await _destination(conn)
    await _destination(conn, types=(FILING_EVENT,))
    off, _ = await _destination(conn)
    await webhook_service.disable_destination(conn, off)
    event_id = await announce(conn, EARNINGS_VERIFIED, Q2_RELEASE)
    assert event_id is not None

    again = await announce(conn, EARNINGS_VERIFIED, Q2_RELEASE)
    queued = await outbox.publish(connect_to(conn), _settings(migrated_database), event_id)
    republished = await outbox.publish(connect_to(conn), _settings(migrated_database), event_id)

    assert again is None and queued == [wanted] and republished == []
    [publish] = await queue.list_tasks(conn, kind=WEBHOOK_PUBLISH)
    assert (publish.key, publish.priority) == (str(event_id), queue.PRIORITY_LIVE)
    [delivery] = await queue.list_tasks(conn, kind=WEBHOOK_DELIVER)
    assert delivery.key == f"{event_id}:{wanted}"
    assert delivery.payload == {"event_id": str(event_id), "dest_id": wanted}
    assert delivery.priority == -5  # after live work and the watches, before backfills
    text = await _payload(conn, event_id)
    body = json.loads(text, parse_float=Decimal)
    assert list(body) == ["id", "type", "created", "livemode", "api_version", "data", "disclaimer"]
    assert (body["id"], body["type"], body["livemode"]) == (str(event_id), EARNINGS_VERIFIED, True)
    assert body["created"].endswith("Z") and body["api_version"] == "v1"
    assert body["disclaimer"] == "Information from public filings. Not investment advice."
    # The object is the API's release object (D-097), numbers digit for digit (D-096).
    api_object = (await get_earnings(conn, Q2_RELEASE)).data.model_dump(mode="python")
    assert body["data"]["object"] == json.loads(dumps(api_object), parse_float=Decimal)
    assert body["data"]["proof_url"] == f"{BASE}/proof/earnings/{Q2_RELEASE}"
    assert '"value":1000,' in text and '"value":1.25,' in text


async def test_a_delivery_is_signed_and_goes_to_the_checked_address(
    conn: Conn, migrated_database: str
) -> None:
    await seed(conn)
    dest_id, secret = await _destination(conn, "https://hooks.example.com:8443/in?client=1")
    event_id = await _published(conn, migrated_database)
    await conn.execute(
        "update webhook_destinations set consecutive_failures = 7 where dest_id = %s", [dest_id]
    )
    endpoint = Receiver(secret)

    attempt = await deliver.deliver(
        connect_to(conn),
        _settings(migrated_database),
        event_id,
        dest_id,
        1,
        final=False,
        transport=httpx2.MockTransport(endpoint),
        resolver=_public,
    )

    [request] = endpoint.requests
    assert str(request.url) == f"https://{PUBLIC_IP}:8443/in?client=1"
    assert request.headers["host"] == "hooks.example.com:8443"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["user-agent"] == "marketdata-webhooks/1"
    assert request.content == (await _payload(conn, event_id)).encode()
    assert (attempt.status_code, attempt.error) == (200, None)
    [logged] = await webhook_service.deliveries(conn, event_id)
    assert (logged.attempt, logged.status_code, logged.error) == (1, 200, None)
    destination = await webhook_service.get_destination(conn, dest_id)
    assert destination.consecutive_failures == 0  # a delivery starts the count again


async def test_failed_attempts_are_retried_after_one_and_ten_minutes_then_dead(
    conn: Conn, migrated_database: str, context: TaskContext
) -> None:
    await seed(conn)
    dest_id, secret = await _destination(conn)
    await _published(conn, migrated_database)
    handler = delivery_handler(httpx2.MockTransport(Receiver(secret, status=503)), _public)
    row = await (await conn.execute("select now()")).fetchone()
    assert row is not None
    now = row[0]  # the transaction's time: every now() of the test

    outcomes = []
    for number in (1, 2, 3):
        await conn.execute("update tasks set run_after = now() where kind = %s", [WEBHOOK_DELIVER])
        task = await queue.claim(conn, [WEBHOOK_DELIVER], timedelta(minutes=5))
        assert task is not None and task.attempts == number
        with pytest.raises(DeliveryFailed, match="answered 503"):
            await handler(context, task)
        failed = await queue.fail(conn, task, "answered 503", DELIVERY_RETRY)
        assert failed is not None
        outcomes.append((failed.status, failed.run_after - now))

    assert outcomes[0] == ("failed", timedelta(minutes=1))
    assert outcomes[1] == ("failed", timedelta(minutes=10))
    assert outcomes[2][0] == "dead"
    attempts = await webhook_service.deliveries(conn, (await _event_ids(conn))[0])
    assert [(row.attempt, row.status_code, row.error) for row in attempts] == [
        (number, 503, "answered 503") for number in (1, 2, 3)
    ]
    # Only the delivery as a whole counts against the destination.
    assert (await webhook_service.get_destination(conn, dest_id)).consecutive_failures == 1


async def _event_ids(conn: Conn) -> list[UUID]:
    rows = await (await conn.execute("select event_id from webhook_events")).fetchall()
    return [row[0] for row in rows]


async def test_fifty_failed_deliveries_in_a_row_switch_the_destination_off(
    conn: Conn, migrated_database: str
) -> None:
    await seed(conn)
    dest_id, secret = await _destination(conn)
    event_id = await _published(conn, migrated_database)
    await conn.execute(
        "update webhook_destinations set consecutive_failures = 49 where dest_id = %s", [dest_id]
    )
    transport = httpx2.MockTransport(Receiver(secret, status=500))

    with capture_logs() as logs, pytest.raises(DeliveryFailed):
        await deliver.deliver(
            connect_to(conn),
            _settings(migrated_database),
            event_id,
            dest_id,
            3,
            final=True,
            transport=transport,
            resolver=_public,
        )

    destination = await webhook_service.get_destination(conn, dest_id)
    assert not destination.active and destination.consecutive_failures == 50
    assert destination.disabled_reason == "50 deliveries in a row failed"
    [alert] = [log for log in logs if log["event"] == "webhook_destination_disabled"]
    assert alert["log_level"] == "error" and alert["dest_id"] == dest_id
    with pytest.raises(DeliveryRefused, match="switched off"):
        await deliver.deliver(
            connect_to(conn),
            _settings(migrated_database),
            event_id,
            dest_id,
            1,
            final=False,
            transport=transport,
            resolver=_public,
        )


async def test_a_private_address_is_refused_and_the_delivery_dies_at_once(
    conn: Conn, migrated_database: str, context: TaskContext
) -> None:
    await seed(conn)
    dest_id, secret = await _destination(conn)
    event_id = await _published(conn, migrated_database)
    endpoint = Receiver(secret)

    async def private(host: str, port: int) -> list[str]:  # noqa: ASYNC124 - a resolver
        return ["10.0.0.7", "fe80::1%en0"]

    task = await queue.claim(conn, [WEBHOOK_DELIVER], timedelta(minutes=5))
    assert task is not None
    with pytest.raises(TaskRefused, match="resolves to no public address"):
        await delivery_handler(httpx2.MockTransport(endpoint), private)(context, task)

    assert endpoint.requests == []
    [logged] = await webhook_service.deliveries(conn, event_id)
    assert logged.status_code is None and logged.error is not None
    assert logged.error.startswith("refused: hooks.example.com resolves to no public address")
    assert "10.0.0.7 (a private or reserved address)" in logged.error
    assert (await webhook_service.get_destination(conn, dest_id)).consecutive_failures == 1


async def test_redirects_are_not_followed(conn: Conn, migrated_database: str) -> None:
    await seed(conn)
    dest_id, secret = await _destination(conn)
    event_id = await _published(conn, migrated_database)
    endpoint = Receiver(secret, status=302, headers={"Location": "https://10.0.0.1/in"})

    with pytest.raises(DeliveryFailed, match="answered 302"):
        await deliver.deliver(
            connect_to(conn),
            _settings(migrated_database),
            event_id,
            dest_id,
            1,
            final=False,
            transport=httpx2.MockTransport(endpoint),
            resolver=_public,
        )

    assert len(endpoint.requests) == 1


async def test_dev_may_deliver_to_this_machine(conn: Conn, migrated_database: str) -> None:
    await seed(conn)
    dest_id, secret = await _destination(conn, "http://127.0.0.1:8765/hook", allow_loopback=True)
    event_id = await _published(conn, migrated_database)
    endpoint = Receiver(secret)

    await deliver.deliver(
        connect_to(conn),
        _settings(migrated_database, env="dev"),
        event_id,
        dest_id,
        1,
        final=False,
        transport=httpx2.MockTransport(endpoint),
        resolver=_public,
    )
    with pytest.raises(DeliveryRefused, match="only ENV=dev"):
        await deliver.deliver(
            connect_to(conn),
            _settings(migrated_database),
            event_id,
            dest_id,
            2,
            final=False,
            transport=httpx2.MockTransport(endpoint),
            resolver=_public,
        )

    [request] = endpoint.requests
    assert str(request.url) == "http://127.0.0.1:8765/hook"
    assert "sni_hostname" not in request.extensions


async def test_a_destination_needs_a_url_deliveries_would_take(conn: Conn) -> None:
    await create_client(conn, "test client")
    for url, why in [
        ("http://hooks.example.com/in", "must use https"),
        ("https://192.168.1.20/in", "public addresses only"),
        ("http://localhost:8765/in", "only ENV=dev"),
    ]:
        with pytest.raises(InvalidParameter, match=why):
            await webhook_service.create_destination(conn, "test client", url, (), secret_enc="x")
    with pytest.raises(InvalidParameter, match="unknown event type earnings.updated"):
        await webhook_service.create_destination(
            conn, "test client", "https://hooks.example.com", ["earnings.updated"], secret_enc="x"
        )
    every = await webhook_service.create_destination(
        conn, "test client", "https://hooks.example.com", (), secret_enc="x"
    )
    assert tuple(every.event_types) == (
        "earnings.verified",
        "financials.updated",
        "data.changed",
        "filing.event",
    )


async def test_a_test_event_goes_to_its_one_destination_once_a_minute(
    conn: Conn, migrated_database: str
) -> None:
    await seed(conn)
    dest_id, _ = await _destination(conn, types=(EARNINGS_VERIFIED, FILING_EVENT))
    await _destination(conn)  # also takes earnings.verified
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)

    first = await webhook_service.queue_test_event(conn, dest_id, now=now)
    with pytest.raises(InvalidParameter, match="one a minute"):
        await webhook_service.queue_test_event(
            conn, dest_id, FILING_EVENT, now=now + timedelta(seconds=59)
        )
    second = await webhook_service.queue_test_event(
        conn, dest_id, FILING_EVENT, now=now + timedelta(seconds=60)
    )
    with pytest.raises(InvalidParameter, match="takes earnings.verified, filing.event"):
        await webhook_service.queue_test_event(
            conn, dest_id, FINANCIALS_UPDATED, now=now + timedelta(minutes=5)
        )

    assert (first.type, first.subject) == (EARNINGS_VERIFIED, Q2_RELEASE)  # the newest verified
    assert (second.type, second.subject) == (FILING_EVENT, f"{Q3_RELEASE}:{ACME}")
    queued = await outbox.publish(connect_to(conn), _settings(migrated_database), first.event_id)
    assert queued == [dest_id]
    body = json.loads(await _payload(conn, first.event_id))
    assert body["livemode"] is False
    await outbox.publish(connect_to(conn), _settings(migrated_database), second.event_id)
    filing = json.loads(await _payload(conn, second.event_id))["data"]["object"]
    assert (filing["ticker"], filing["filing"]["accession_no"]) == ("ACME", Q3_RELEASE)
    assert {"item_or_form": "2.02", "label": "Quarterly or annual results"}.items() <= (
        filing["filing"]["events"][0].items()
    )


async def _apple(conn: Conn) -> None:
    await conn.execute(
        "insert into companies (cik, name, sic_code, fiscal_year_end, filer_type, in_universe) "
        "values (%s, 'Apple Inc.', '3571', '0926', 'domestic', true)",
        [AAPL],
    )


async def _extract(conn: Conn, tmp_path: Path, accession: str, *, live: bool) -> None:
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await EarningsIngest(fetcher, connect_to(conn)).run(AAPL, accession, live=live)


async def test_a_live_release_is_announced_once_and_a_backfilled_one_never(
    conn: Conn, tmp_path: Path
) -> None:
    await _apple(conn)

    await _extract(conn, tmp_path, AAPL_RELEASE, live=True)
    await _extract(conn, tmp_path, AAPL_RELEASE, live=False)  # a forced re-run: still live
    await _extract(conn, tmp_path, AAPL_OLDER, live=False)  # found by the backfill

    assert await _events(conn, EARNINGS_VERIFIED) == [(AAPL_RELEASE, {}, True)]
    live = await (
        await conn.execute("select accession_no, live from earnings_releases order by accession_no")
    ).fetchall()
    assert live == [(AAPL_OLDER, False), (AAPL_RELEASE, True)]
    [publish] = await queue.list_tasks(conn, kind=WEBHOOK_PUBLISH)
    assert publish.priority == queue.PRIORITY_LIVE


async def test_a_facts_refresh_announces_new_rows_and_changed_values(
    conn: Conn, tmp_path: Path
) -> None:
    await _apple(conn)
    routes: dict[str, bytes | int] = {urls.companyfacts(AAPL): AAPL_FACTS}

    async def refresh() -> None:
        async with sec_serving(routes) as sec:
            fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
            await FactsLoader(fetcher, connect_to(conn)).refresh(
                AAPL, now=datetime(2026, 8, 1, 12, tzinfo=UTC)
            )

    await refresh()
    await refresh()  # nothing new
    await rebuild_all(connect_to(conn), [AAPL])  # `normalize`: rows rebuilt, none announced

    [(subject, first, _)] = await _events(conn, FINANCIALS_UPDATED)
    assert subject.startswith(f"{AAPL}:")
    assert first["new"] > 1000 and first["changed"] == 0
    assert {"revenue", "net_income", "eps_diluted"} <= set(first["metrics"])
    assert first["filings"][0] == "0000320193-26-000020"  # the newest 10-Q in the fixture

    await conn.execute(
        "update financial_metrics set value = value + 1 "
        "where cik = %s and metric = 'revenue' and period_type = 'Q' and period_end = '2026-06-27'",
        [AAPL],
    )
    await refresh()

    # One transaction: both events have its time, so they are told apart by what they say.
    details = [detail for _, detail, _ in await _events(conn, FINANCIALS_UPDATED)]
    [changed] = [detail for detail in details if detail["new"] == 0]
    assert len(details) == 2 and changed["changed"] == 1
    assert changed["metrics"] == ["revenue"]
    assert changed["periods"] == [{"period_type": "Q", "period_end": "2026-06-27"}]


async def test_a_watch_announces_a_new_filing_with_a_high_label(
    conn: Conn, tmp_path: Path, load_master: LoadMaster, migrated_database: str
) -> None:
    await load_master()
    kinds = frozenset({EARNINGS_EXTRACT})

    async def watch(body: bytes) -> None:
        async with sec_serving({urls.submissions(AAPL): body}) as sec:
            fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
            await FilingsWatcher(fetcher, connect_to(conn), kinds).watch(AAPL)

    document = json.loads(AAPL_SUBMISSIONS)
    recent = document["filings"]["recent"]
    index = recent["accessionNumber"].index(AAPL_RELEASE)
    for values in recent.values():
        del values[index]
    await watch(json.dumps(document).encode())  # the first watch announces nothing
    assert await _events(conn, FILING_EVENT) == []

    await watch(AAPL_SUBMISSIONS)

    [(subject, _, live)] = await _events(conn, FILING_EVENT)
    assert (subject, live) == (f"{AAPL_RELEASE}:{AAPL}", True)
    [task] = await queue.list_tasks(conn, kind=EARNINGS_EXTRACT)
    assert task.payload["live"] is True
    [event_id] = await _event_ids(conn)
    await outbox.publish(connect_to(conn), _settings(migrated_database), event_id)
    body = json.loads(await _payload(conn, event_id))
    announced = body["data"]["object"]
    assert (announced["ticker"], announced["cik"]) == ("AAPL", AAPL)
    assert announced["filing"]["form"] == "8-K" and body["data"]["proof_url"] is None
