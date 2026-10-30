"""E5b-S1: webhook rules (PLAN.md §9.11): where deliveries may go, what a facts refresh
announces, which filings are events, the signature, the encrypted secret and the reference
receiver that checks it."""

import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.fernet import Fernet

from marketdata.config import SettingsError, load_settings, require_webhook_key
from marketdata.normalize.changes import StoredMetric
from marketdata.normalize.metrics import MetricRow
from marketdata.normalize.webhooks import (
    HIGH_ITEMS,
    announces_filing,
    check_url,
    metrics_update,
    refused_address,
)
from marketdata.webhooks.signing import (
    SecretUnreadable,
    decrypt_secret,
    encrypt_secret,
    new_secret,
    signature,
)

ROOT = Path(__file__).resolve().parents[2]
URL = "postgresql://marketdata:marketdata@localhost:5432/marketdata"


def _load_receiver() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "webhook_receiver", ROOT / "scripts" / "webhook_receiver.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


receiver = _load_receiver()


@pytest.mark.parametrize(
    ("url", "checked"),
    [
        ("https://hooks.example.com/marketdata", "https://hooks.example.com/marketdata"),
        (" HTTPS://Hooks.Example.com ", "https://hooks.example.com/"),
        (
            "https://hooks.example.com:8443/in?client=1",
            "https://hooks.example.com:8443/in?client=1",
        ),
        ("https://8.8.8.8/hooks", "https://8.8.8.8/hooks"),
        ("https://[2001:4860:4860::8888]/hooks", "https://[2001:4860:4860::8888]/hooks"),
    ],
)
def test_https_urls_are_kept(url: str, checked: str) -> None:
    assert check_url(url) == checked


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://hooks.example.com/in", "must use https"),
        ("ftp://hooks.example.com/in", "must be an https URL"),
        ("https:///in", "must be an https URL with a host"),
        ("https://user:pw@hooks.example.com/in", "user name or password"),
        ("https://hooks.example.com/in#x", "#fragment"),
        ("https://hooks.example.com:99999/in", "is not a URL"),
        ("https://localhost/in", "only ENV=dev"),
        ("https://127.0.0.1/in", "only ENV=dev"),
        ("https://10.1.2.3/in", "private or reserved"),
        ("https://192.168.0.10/in", "private or reserved"),
        ("https://169.254.169.254/latest/meta-data", "link-local"),
        ("https://100.64.0.1/in", "private or reserved"),
        ("https://[fd00::1]/in", "private or reserved"),
        ("https://[::ffff:10.0.0.1]/in", "private or reserved"),
        ("https://0.0.0.0/in", "private or reserved"),
    ],
)
def test_urls_deliveries_would_refuse(url: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        check_url(url)


def test_dev_may_deliver_to_this_machine_only() -> None:
    assert check_url("http://localhost:8765/hook", allow_loopback=True) == (
        "http://localhost:8765/hook"
    )
    assert check_url("http://127.0.0.1:8765", allow_loopback=True) == "http://127.0.0.1:8765/"
    assert check_url("http://[::1]:8765/", allow_loopback=True) == "http://[::1]:8765/"
    with pytest.raises(ValueError, match="private or reserved"):
        check_url("https://10.0.0.5/hook", allow_loopback=True)
    with pytest.raises(ValueError, match="must use https"):
        check_url("http://hooks.example.com/hook", allow_loopback=True)


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("8.8.8.8", None),
        ("2606:4700:4700::1111", None),
        ("127.0.0.1", "a loopback address"),
        ("::1", "a loopback address"),
        ("10.0.0.1", "a private or reserved address"),
        ("172.16.5.4", "a private or reserved address"),
        ("192.0.2.1", "a private or reserved address"),
        ("100.100.0.1", "a private or reserved address"),
        ("169.254.169.254", "a link-local address"),
        ("fe80::1", "a link-local address"),
        ("224.0.0.1", "a multicast address"),
        ("::ffff:127.0.0.1", "a loopback address"),
    ],
)
def test_only_public_addresses_take_deliveries(address: str, reason: str | None) -> None:
    assert refused_address(address) == reason


def test_loopback_is_allowed_in_dev() -> None:
    assert refused_address("127.0.0.1", allow_loopback=True) is None
    assert refused_address("10.0.0.1", allow_loopback=True) == "a private or reserved address"


def test_filings_with_a_high_label_are_events() -> None:
    assert announces_filing("8-K", ("2.02", "9.01"), None)
    assert announces_filing("8-K", ("5.01",), None)
    assert not announces_filing("8-K", ("7.01", "9.01"), None)
    assert not announces_filing("10-Q", None, "001-36743")
    # A 13D is an event on the listing of the company it is about, not on the filer's.
    assert announces_filing("SCHEDULE 13D", None, "005-90876")
    assert not announces_filing("SCHEDULE 13D", None, None)
    assert not announces_filing("SC 13G/A", None, "005-90876")
    assert "2.02" in HIGH_ITEMS and "9.01" not in HIGH_ITEMS


def _row(metric: str, end: date, value: str | None, accession: str = "a-2") -> MetricRow:
    return MetricRow(
        cik=1,
        metric=metric,
        period_type="Q",
        fiscal_year=end.year,
        fiscal_period="Q2",
        period_start=None,
        period_end=end,
        value=Decimal(value) if value is not None else None,
        unit="USD",
        concept="Revenues",
        accession_no=accession,
        source_accessions=(accession,),
        filed_at=date(2026, 8, 1) if accession == "a-2" else date(2025, 8, 1),
    )


def test_a_refresh_announces_new_rows_and_changed_values_only() -> None:
    q2, q2_before = date(2026, 6, 30), date(2025, 6, 30)
    stored = {
        ("revenue", "Q", q2_before): StoredMetric(Decimal(90), "a-1", date(2025, 8, 1), "2.3"),
        ("net_income", "Q", q2_before): StoredMetric(Decimal(9), "a-1", date(2025, 8, 1), "2.3"),
    }
    rows = [
        _row("revenue", q2, "100"),  # new
        _row("revenue", q2_before, "90"),  # filed again with the same value: not announced
        _row("net_income", q2_before, "8"),  # changed
    ]

    update = metrics_update(1, stored, rows)

    assert update is not None
    assert (update.new, update.changed) == (1, 1)
    assert update.metrics == ("net_income", "revenue")
    assert update.periods == (("Q", q2), ("Q", q2_before))
    assert update.filings == ("a-2",)
    assert update.detail()["periods"][0] == {"period_type": "Q", "period_end": "2026-06-30"}
    assert update.subject == f"1:{update.digest}"
    assert metrics_update(1, stored, list(reversed(rows))) == update  # the order doesn't matter
    assert metrics_update(1, stored, [_row("revenue", q2_before, "90")]) is None


def test_the_signature_is_what_the_reference_receiver_checks() -> None:
    secret = new_secret()
    body = b'{"id":"e1","type":"earnings.verified"}'
    header = signature(secret, body, 1_800_000_000)

    assert secret.startswith("whsec_") and len(secret) > 40
    assert header.startswith("t=1800000000,v1=") and len(header.split("v1=")[1]) == 64
    receiver.verify(secret, header, body, now=1_800_000_000 + 299)
    for wrong_secret, wrong_body, now, why in [
        ("whsec_other", body, 1_800_000_000, "doesn't match"),
        (secret, body + b" ", 1_800_000_000, "doesn't match"),
        (secret, body, 1_800_000_000 + 301, "five minutes"),
    ]:
        with pytest.raises(receiver.InvalidSignature, match=why):
            receiver.verify(wrong_secret, header, wrong_body, now=now)
    with pytest.raises(receiver.InvalidSignature, match="no X-Signature"):
        receiver.verify(secret, None, body)
    with pytest.raises(receiver.InvalidSignature, match="must be t="):
        receiver.verify(secret, "v1=abc", body)


def test_the_receiver_prints_one_line_per_event() -> None:
    event = {
        "id": "e1",
        "type": "earnings.verified",
        "livemode": False,
        "data": {
            "object": {"ticker": "AAPL", "release": {"accession_no": "0000320193-26-000018"}},
            "proof_url": None,
        },
    }

    assert receiver.summary(json.loads(json.dumps(event))) == (
        "e1 earnings.verified (test) AAPL 0000320193-26-000018"
    )


def test_secrets_are_stored_encrypted_with_the_key() -> None:
    key = Fernet.generate_key().decode()
    token = encrypt_secret(key, "whsec_abc")

    assert "whsec_abc" not in token
    assert decrypt_secret(key, token) == "whsec_abc"
    with pytest.raises(SecretUnreadable, match="isn't the one it was created with"):
        decrypt_secret(Fernet.generate_key().decode(), token)


def test_the_encryption_key_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    with pytest.raises(SettingsError, match="WEBHOOK_ENCRYPTION_KEY is missing"):
        require_webhook_key(load_settings(env_file=None))

    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", "not-a-key-at-all")
    with pytest.raises(SettingsError, match="WEBHOOK_ENCRYPTION_KEY is invalid") as excinfo:
        load_settings(env_file=None)
    assert "not-a-key-at-all" not in str(excinfo.value)

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://data.example.com/")
    settings = load_settings(env_file=None)
    assert require_webhook_key(settings) == key
    assert settings.base_url == "https://data.example.com"
