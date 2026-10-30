"""Webhook signatures and signing secrets (PLAN.md §9.11, D-019; E5b-S1).

Every delivery is signed with its destination's secret, as written (`whsec_…`):

    X-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">

A receiver computes the same HMAC over the raw body it received, compares it in constant time
and rejects a `t` more than five minutes old (scripts/webhook_receiver.py is the reference).
The secret is printed once when the destination is created and stored only encrypted with
WEBHOOK_ENCRYPTION_KEY (Fernet: AES-128 with an HMAC-SHA256 over the token).
"""

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

SIGNATURE_HEADER = "X-Signature"
SECRET_PREFIX = "whsec_"
TOLERANCE_SECONDS = 300  # receivers reject older signatures


class SecretUnreadable(Exception):
    """A stored secret that this WEBHOOK_ENCRYPTION_KEY can't decrypt: the key changed."""


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def encrypt_secret(key: str, secret: str) -> str:
    return Fernet(key.encode("ascii")).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(key: str, token: str) -> str:
    try:
        return Fernet(key.encode("ascii")).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise SecretUnreadable(
            "the destination's signing secret can't be decrypted with WEBHOOK_ENCRYPTION_KEY: "
            "the key isn't the one it was created with"
        ) from None


def signature(secret: str, body: bytes, timestamp: int) -> str:
    """The X-Signature header for `body` sent at `timestamp` (unix seconds)."""
    signed = f"{timestamp}.".encode("ascii") + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"
