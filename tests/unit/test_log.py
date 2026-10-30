import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from marketdata.config import Env
from marketdata.log import configure_logging


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


@pytest.mark.parametrize("env", ["live", "prod"])
def test_json_in_live_and_prod(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(env, "INFO")

    structlog.get_logger("marketdata.test").info("task_done", cik=320193)
    logging.getLogger("uvicorn.error").warning("from stdlib")

    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert lines[0]["event"] == "task_done"
    assert lines[0]["cik"] == 320193
    assert lines[0]["level"] == "info"
    assert lines[0]["timestamp"].endswith("Z")
    assert lines[1]["event"] == "from stdlib"
    assert lines[1]["logger"] == "uvicorn.error"


def test_readable_lines_in_dev(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("dev", "INFO")

    structlog.get_logger("marketdata.test").info("task_done", cik=320193)

    line = capsys.readouterr().err.strip()
    assert "task_done" in line
    assert "cik=320193" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_level_filters(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("prod", "WARNING")

    structlog.get_logger("marketdata.test").info("hidden")

    assert capsys.readouterr().err == ""


def _fail_with(signing_secret: str) -> None:
    raise RuntimeError("delivery failed")


@pytest.mark.parametrize("env", ["dev", "live"])
def test_tracebacks_carry_no_local_variables(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(env, "INFO")
    log = structlog.get_logger("test")
    secret = "-".join(("whsec_do", "not", "log", "me"))  # only ever whole at run time

    try:
        _fail_with(secret)
    except RuntimeError:
        log.exception("task_failed")

    out = capsys.readouterr().err
    assert "delivery failed" in out and "_fail_with" in out  # the frame is named
    assert secret not in out  # the values of its locals aren't
