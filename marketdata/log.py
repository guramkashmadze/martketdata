"""Structured logging: JSON in live and prod, readable lines in dev.

Standard-library loggers (uvicorn, psycopg, alembic) go through the same formatter, so every
line has the same shape. Logs go to stderr; stdout stays free for command output.

A logged traceback names its frames and lines but never their local variables: a frame can hold
a secret in the clear (a webhook's signing secret while it signs a delivery, E5b), and secrets
are never logged (PLAN.md §6).

uvicorn's access log names each request's client address; it is cut short before it is written
(E15, §9.14: server logs only, with IP addresses truncated): an IPv4 address loses its last byte,
an IPv6 address keeps its first 48 bits, and the port is dropped.
"""

import ipaddress
import logging
import sys

import structlog
from structlog.typing import Processor

from marketdata.config import Env, LogLevel

# Loggers that uvicorn configures with its own handlers before it imports the app.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_IPV4_KEPT, _IPV6_KEPT = 24, 48  # bits of an address the access log keeps


def short_address(client: str) -> str:
    """A client address as the access log keeps it: `203.0.113.57:51234` gives `203.0.113.0`,
    an IPv6 address its first 48 bits (`2001:db8:85a3::`); anything that isn't an address,
    `-`. uvicorn writes the address and port as `host:port`, an IPv6 host without brackets."""
    host, _, port = client.rpartition(":")
    for text in (host, client) if port.isdigit() else (client,):
        try:
            address = ipaddress.ip_address(text.strip("[]"))
        except ValueError:
            continue
        kept = _IPV4_KEPT if address.version == 4 else _IPV6_KEPT
        return str(ipaddress.ip_network(f"{address}/{kept}", strict=False).network_address)
    return "-"


class _ShortClientAddress(logging.Filter):
    """Cuts the client address of uvicorn's access log lines (`'%s - "%s %s HTTP/%s" %d'`, the
    address first) short before any handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args and isinstance(record.args[0], str):
            record.args = (short_address(record.args[0]), *record.args[1:])
        return True


def configure_logging(env: Env, level: LogLevel) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: list[Processor]
    if env == "dev":
        renderer = [
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(),
                exception_formatter=structlog.dev.RichTracebackFormatter(show_locals=False),
            )
        ]
    else:
        renderer = [
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(each, _ShortClientAddress) for each in access.filters):
        access.addFilter(_ShortClientAddress())
