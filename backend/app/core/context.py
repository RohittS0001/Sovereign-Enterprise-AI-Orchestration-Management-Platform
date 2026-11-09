"""Per-request context shared across the call stack (logging, auditing, tracing)."""

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.core.security.permissions import Principal

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Set by ToolGateway while a tool runs, so tools that must enforce per-user access
# (e.g. knowledge search) know who they are acting for.
principal_var: ContextVar["Principal | None"] = ContextVar("principal", default=None)


def get_request_id() -> str | None:
    """Return the ID of the request currently being handled, if any."""
    return request_id_var.get()


def get_principal() -> "Principal | None":
    return principal_var.get()
