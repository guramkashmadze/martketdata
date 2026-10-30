"""A reference webhook receiver (PLAN.md §9.11; E5b-S1): it checks each delivery's signature as
a client should, then prints the event. The tests verify our deliveries with `verify`.

    python scripts/webhook_receiver.py --port 8765

The signing secret comes from WEBHOOK_SECRET (as `marketdata webhooks create` printed it).
A delivery whose signature is valid and recent is answered 200, anything else 400. A receiver:

- computes HMAC-SHA256 over "<t>.<raw body>", keyed with the secret, and compares it with the
  `v1` of the X-Signature header in constant time;
- rejects a `t` more than five minutes from its own clock (a replayed delivery);
- dedupes on the event's `id`: a delivery can come twice, and the order isn't guaranteed.

Only the standard library is used, so the script runs anywhere Python does.
"""

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SIGNATURE_HEADER = "X-Signature"
TOLERANCE_SECONDS = 300


class InvalidSignature(Exception):
    pass


def verify(secret: str, header: str | None, body: bytes, now: float | None = None) -> None:
    """Raise InvalidSignature unless `header` signs `body` with `secret` within five minutes."""
    if not header:
        raise InvalidSignature(f"no {SIGNATURE_HEADER} header")
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp, given = int(parts["t"]), parts["v1"]
    except KeyError, ValueError:
        raise InvalidSignature(f"{SIGNATURE_HEADER} must be t=<unix seconds>,v1=<hex>") from None
    clock = time.time() if now is None else now
    if abs(clock - timestamp) > TOLERANCE_SECONDS:
        raise InvalidSignature("the signature is more than five minutes from now")
    signed = f"{timestamp}.".encode("ascii") + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise InvalidSignature("the signature doesn't match the body")


def summary(event: dict[str, Any]) -> str:
    """One line about an event: its id, type, whether it is live and what it is about."""
    data = event.get("data") or {}
    item = data.get("object") or {}
    about = item.get("ticker") or item.get("company") or ""
    release = item.get("release") or {}
    filing = item.get("filing") or {}
    accession = release.get("accession_no") or filing.get("accession_no") or ""
    live = "live" if event.get("livemode") else "test"
    proof = data.get("proof_url") or ""
    return f"{event.get('id')} {event.get('type')} ({live}) {about} {accession} {proof}".strip()


class Receiver(BaseHTTPRequestHandler):
    secret = ""
    seen: set[str] = set()

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            verify(self.secret, self.headers.get(SIGNATURE_HEADER), body)
            event = json.loads(body)
        except (InvalidSignature, ValueError) as exc:
            print(f"refused: {exc}", flush=True)
            self.send_response(400)
            self.end_headers()
            return
        repeat = " (again: deduped on its id)" if event.get("id") in self.seen else ""
        self.seen.add(str(event.get("id")))
        print(summary(event) + repeat, flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # one line per event is printed instead


def main() -> int:
    parser = argparse.ArgumentParser(description="A reference webhook receiver.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        print("Set WEBHOOK_SECRET to the secret `marketdata webhooks create` printed.")
        return 2
    Receiver.secret = secret
    server = ThreadingHTTPServer((args.host, args.port), Receiver)
    print(f"Listening on http://{args.host}:{args.port}/ (Ctrl-C stops).", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
