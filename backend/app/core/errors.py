"""Application exception hierarchy.

Every error raised deliberately by the platform derives from ``AppException`` so the
API layer can turn it into a consistent JSON error response with a stable ``code``.
"""

from typing import Any


class AppException(Exception):
    """Base exception for application-level errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message
        self.headers = headers or {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details

        super().__init__(message)


class ConfigurationError(AppException):
    """Raised when application configuration is missing or invalid."""

    status_code = 500
    code = "configuration_error"


class IntegrationError(AppException):
    """Raised when an external integration (Jira, Gmail, ...) fails."""

    status_code = 502
    code = "integration_error"

    def __init__(
        self,
        message: str,
        *,
        integration: str,
        upstream_status: int | None = None,
        details: Any = None,
    ) -> None:
        self.integration = integration
        self.upstream_status = upstream_status
        super().__init__(message, details=details)


class ToolExecutionError(AppException):
    """Raised when an MCP/tool operation fails."""

    status_code = 500
    code = "tool_execution_error"


class RetrievalError(AppException):
    """Raised when knowledge retrieval fails."""

    status_code = 500
    code = "retrieval_error"


class NotFoundError(AppException):
    """Raised when a requested resource does not exist."""

    status_code = 404
    code = "not_found"


class AuthenticationError(AppException):
    """Raised when a caller cannot be authenticated."""

    status_code = 401
    code = "authentication_error"


class AuthorizationError(AppException):
    """Raised when an authenticated caller lacks permission."""

    status_code = 403
    code = "authorization_error"


class ConflictError(AppException):
    """Raised when a request conflicts with existing state (e.g. duplicate email)."""

    status_code = 409
    code = "conflict"


class RateLimitError(AppException):
    """Raised when a caller exceeds a rate limit."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many requests; try again later",
            details={"retry_after_seconds": retry_after_seconds},
            headers={"Retry-After": str(retry_after_seconds)},
        )


class ServiceUnavailableError(AppException):
    """Raised when a required backing service is not configured or reachable."""

    status_code = 503
    code = "service_unavailable"
