"""Structured logging: JSON in live and prod, readable lines in dev.

Standard-library loggers (uvicorn, psycopg, alembic) go through the same formatter, so every
line has the same shape. Logs go to stderr; stdout stays free for command output.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from marketdata.config import Env, LogLevel

# Loggers that uvicorn configures with its own handlers before it imports the app.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(env: Env, level: LogLevel) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: list[Processor]
    if env == "dev":
        renderer = [structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())]
    else:
        renderer = [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]

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
