"""Structured logging.

``configure_logging`` installs a single root handler that emits JSON (or readable text
in development). Every record carries the current request ID so log lines from one
request can be correlated. Modules keep using ``get_logger(__name__)``.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from backend.app.core.context import get_request_id

# Attributes present on every LogRecord; anything else was passed via ``extra=``.
_RESERVED_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)

_HANDLER_NAME = "sovereign-root-handler"


class RequestIdFilter(logging.Filter):
    """Attach the active request ID to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        from backend.app.core.observability import current_trace_id

        trace_id = current_trace_id()
        if trace_id:
            record.trace_id = trace_id
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in vars(record).items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s"
        )


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install (or replace) the root log handler. Safe to call more than once."""
    root = logging.getLogger()

    for handler in list(root.handlers):
        if handler.get_name() == _HANDLER_NAME:
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())

    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Return a module logger; output goes through the root handler."""
    return logging.getLogger(name)
