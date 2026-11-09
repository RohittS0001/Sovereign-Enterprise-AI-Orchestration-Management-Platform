"""Exception handlers that give every API error the same JSON shape.

{"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.context import get_request_id
from backend.app.core.exceptions.exceptions import AppException
from backend.app.core.logging.logger import get_logger

logger = get_logger(__name__)

_HTTP_CODES = {
    400: "bad_request",
    401: "authentication_error",
    403: "authorization_error",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": jsonable_encoder(details),
                "request_id": get_request_id(),
            }
        },
    )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    log = logger.error if exc.status_code >= 500 else logger.warning
    log(
        "Application error: %s",
        exc.message,
        extra={"error_code": exc.code, "path": request.url.path},
    )
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed", details)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    response = error_response(exc.status_code, code, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


def register_exception_handlers(app: FastAPI) -> None:
    # Unexpected exceptions are turned into a generic 500 (without leaking internal
    # error text) by RequestContextMiddleware, so they keep their request ID.
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
