import json
import logging

from backend.app.core.context import request_id_var
from backend.app.core.logging import JsonFormatter, RequestIdFilter, configure_logging


def _record(message: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_core_fields_and_extras() -> None:
    record = _record(path="/api/v1/health", status_code=200)
    RequestIdFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "hello"
    assert payload["path"] == "/api/v1/health"
    assert payload["status_code"] == 200
    assert "timestamp" in payload


def test_request_id_is_attached_from_context() -> None:
    token = request_id_var.set("req-123")
    try:
        record = _record()
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)

    assert json.loads(JsonFormatter().format(record))["request_id"] == "req-123"


def test_json_formatter_includes_exception() -> None:
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    payload = json.loads(JsonFormatter().format(record))

    assert "RuntimeError: kaboom" in payload["exception"]


def test_configure_logging_is_idempotent_and_honours_level() -> None:
    root = logging.getLogger()
    original_level = root.level
    try:
        configure_logging("WARNING", "json")
        configure_logging("DEBUG", "text")

        ours = [h for h in root.handlers if h.get_name() == "sovereign-root-handler"]
        assert len(ours) == 1
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(original_level)
