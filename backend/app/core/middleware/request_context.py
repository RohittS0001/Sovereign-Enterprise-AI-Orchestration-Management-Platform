"""ASGI middleware that assigns a request ID and writes one access-log line per request."""

import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.core.context import request_id_var
from backend.app.core.error_handlers import error_response
from backend.app.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"

# Accept caller-supplied IDs only if they are short and safe to echo into logs/headers.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = get_logger("backend.access")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, REQUEST_ID_HEADER)
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        )
        token = request_id_var.set(request_id)

        status_code = 500
        response_started = False
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # Handle unexpected errors here (rather than in Starlette's outermost
            # ServerErrorMiddleware) so the 500 response still carries the request ID.
            logger.exception("Unhandled error", extra={"path": scope["path"]})
            if response_started:
                raise
            response = error_response(500, "internal_error", "An unexpected error occurred")
            await response(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "%s %s -> %s",
                scope["method"],
                scope["path"],
                status_code,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)


def _header(scope: Scope, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key == target:
            return value.decode("latin-1")
    return None
