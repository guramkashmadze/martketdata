"""Webhook rules (PLAN.md §9.11, D-019; E5b): the event types, what each one announces, and
where a delivery may go.

Pure. The code that changes the data writes the event in the same transaction
(ingest/announce.py); the worker publishes it (`webhook_publish`: its body built through the
service layer, the same objects as the API) and delivers it to each active destination that
subscribed to its type (`webhook_deliver`, three attempts about 1 and 10 minutes apart).

- `earnings.verified`: a release the watcher routed when it was filed (`live`) is complete or
  partial. Announced once per release; a backfill or a re-run announces nothing, though the
  values it changes go out as `data.changed`.
- `financials.updated`: a facts refresh (after a 10-Q or 10-K) added metric rows or changed a
  value. The event names the filings, metrics and periods; the rows are served by the API.
- `data.changed`: a stored value changed (value_changes, D-027); one event per change.
- `filing.event`: a filing a watch found has a high-importance label (§9.6).

Deliveries go to HTTPS URLs whose host is a public address: private, loopback, link-local,
shared and reserved addresses are refused, also when a name resolves to one. In dev, http and
https to this machine (localhost, 127.0.0.1, ::1) are allowed for the local demo and its
reference receiver.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from marketdata.normalize.changes import StoredMetric
from marketdata.normalize.filing_events import ITEM_LABELS, filing_events
from marketdata.normalize.metrics import MetricKey, MetricRow

type EventType = Literal["earnings.verified", "financials.updated", "data.changed", "filing.event"]
EARNINGS_VERIFIED: EventType = "earnings.verified"
FINANCIALS_UPDATED: EventType = "financials.updated"
DATA_CHANGED: EventType = "data.changed"
FILING_EVENT: EventType = "filing.event"
EVENT_TYPES: tuple[EventType, ...] = (
    EARNINGS_VERIFIED,
    FINANCIALS_UPDATED,
    DATA_CHANGED,
    FILING_EVENT,
)

# Internal task kinds (PLAN.md §4): publish an event (key: event id), then deliver it to one
# destination (key: event id:destination id).
WEBHOOK_PUBLISH = "webhook_publish"
WEBHOOK_DELIVER = "webhook_deliver"

# 8-K items whose §9.6 label is high importance (a test filing.event finds one with these).
HIGH_ITEMS: tuple[str, ...] = tuple(
    code for code, label in ITEM_LABELS.items() if label.importance == "high"
)

API_VERSION = "v1"
LOOPBACK_NAMES = frozenset({"localhost"})


def delivery_key(event_id: object, dest_id: int) -> str:
    return f"{event_id}:{dest_id}"


def check_url(url: str, *, allow_loopback: bool = False) -> str:
    """The destination URL, checked: https with a host, no user name or password, no fragment,
    and no address that a delivery would refuse. `allow_loopback` (dev only) also lets http
    and https reach this machine. Raises ValueError saying what is wrong."""
    text = url.strip()
    try:
        parts = urlsplit(text)
        port = parts.port  # a port that isn't a number raises
    except ValueError:
        raise ValueError(f"{text!r} is not a URL") from None
    scheme, host = parts.scheme.lower(), parts.hostname
    if scheme not in ("https", "http") or not host:
        raise ValueError(
            f"{text!r} must be an https URL with a host, e.g. https://example.com/hooks"
        )
    if parts.username is not None or parts.password is not None:
        raise ValueError("the URL must not carry a user name or password")
    if parts.fragment:
        raise ValueError("the URL must not have a #fragment")
    loopback = is_loopback_host(host)
    if loopback and not allow_loopback:
        raise ValueError(f"{host} is this machine; only ENV=dev delivers there")
    if scheme == "http" and not loopback:
        raise ValueError("the URL must use https")
    literal = _address(host)
    if literal is not None:
        reason = refused_address(str(literal), allow_loopback=allow_loopback)
        if reason is not None:
            raise ValueError(f"{host} is {reason}; deliveries go to public addresses only")
    return urlunsplit((scheme, netloc_of(host, port), parts.path or "/", parts.query, ""))


def netloc_of(host: str, port: int | None) -> str:
    """`host[:port]`, an IPv6 address in brackets."""
    name = f"[{host}]" if ":" in host else host
    return name if port is None else f"{name}:{port}"


def is_loopback_host(host: str) -> bool:
    address = _address(host)
    return host.lower() in LOOPBACK_NAMES if address is None else address.is_loopback


def refused_address(text: str, *, allow_loopback: bool = False) -> str | None:
    """Why a delivery may not connect to this IP address, or None when it may: only globally
    reachable unicast addresses are allowed (and loopback with `allow_loopback`)."""
    address: IPv4Address | IPv6Address = ip_address(text)
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return None if allow_loopback else "a loopback address"
    if address.is_multicast:
        return "a multicast address"
    if address.is_link_local:
        return "a link-local address"
    if not address.is_global:
        return "a private or reserved address"
    return None


def is_ip_address(host: str) -> bool:
    return _address(host) is not None


def _address(host: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(host.strip("[]"))
    except ValueError:
        return None


def announces_filing(form: str, items_8k: Sequence[str] | None, file_number: str | None) -> bool:
    """Whether a new filing is a `filing.event`: one of its §9.6 labels is high importance."""
    return any(event.importance == "high" for event in filing_events(form, items_8k, file_number))


@dataclass(frozen=True)
class MetricsUpdate:
    """What a rebuild after a facts refresh added or changed, for `financials.updated`."""

    cik: int
    new: int  # rows whose period wasn't stored
    changed: int  # stored rows whose value changed
    metrics: tuple[str, ...]
    periods: tuple[tuple[str, date], ...]  # period type and end, newest first
    filings: tuple[str, ...]  # the filings those rows read last, newest first
    digest: str  # of the rows and values: one event per distinct update

    @property
    def subject(self) -> str:
        return f"{self.cik}:{self.digest}"

    def detail(self) -> dict[str, Any]:
        return {
            "cik": self.cik,
            "new": self.new,
            "changed": self.changed,
            "metrics": list(self.metrics),
            "periods": [
                {"period_type": period_type, "period_end": end.isoformat()}
                for period_type, end in self.periods
            ],
            "filings": list(self.filings),
        }


def metrics_update(
    cik: int, stored: Mapping[MetricKey, StoredMetric], rows: Sequence[MetricRow]
) -> MetricsUpdate | None:
    """The rows of a rebuild that are new or carry another value than the stored row; None
    when there are none. A row whose source or flags changed but not its value doesn't count:
    each new 10-Q files the year-ago quarter again."""
    new = [row for row in rows if row.key not in stored]
    changed = [row for row in rows if row.key in stored and stored[row.key].value != row.value]
    touched = sorted([*new, *changed], key=lambda row: row.key)
    if not touched:
        return None
    filed = {row.accession_no: row.filed_at or date.min for row in touched if row.accession_no}
    newest_first = sorted(filed, key=lambda accession: (filed[accession], accession), reverse=True)
    digest = hashlib.sha256(
        "\n".join(
            f"{row.metric}|{row.period_type}|{row.period_end}|{row.value}|{row.accession_no}"
            for row in touched
        ).encode()
    ).hexdigest()[:16]
    return MetricsUpdate(
        cik=cik,
        new=len(new),
        changed=len(changed),
        metrics=tuple(sorted({row.metric for row in touched})),
        periods=tuple(
            sorted(
                {(row.period_type, row.period_end) for row in touched},
                key=lambda period: (period[1], period[0]),
                reverse=True,
            )
        ),
        filings=tuple(newest_first),
        digest=digest,
    )
