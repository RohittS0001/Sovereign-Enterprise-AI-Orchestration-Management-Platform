"""Per-request context shared across the call stack (logging, auditing, tracing)."""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the ID of the request currently being handled, if any."""
    return request_id_var.get()
