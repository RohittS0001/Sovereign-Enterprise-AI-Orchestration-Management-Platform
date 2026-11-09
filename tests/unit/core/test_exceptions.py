import pytest

from backend.app.core.errors import (
    AppException,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    IntegrationError,
    NotFoundError,
    RetrievalError,
    ToolExecutionError,
)


def test_app_exception_defaults() -> None:
    exc = AppException("boom")

    assert exc.message == "boom"
    assert exc.status_code == 500
    assert exc.code == "internal_error"
    assert exc.details is None
    assert str(exc) == "boom"


def test_app_exception_keeps_positional_status_code() -> None:
    # Existing callers pass the status code positionally.
    exc = AppException("bad input", 422, code="validation_error", details={"field": "q"})

    assert exc.status_code == 422
    assert exc.code == "validation_error"
    assert exc.details == {"field": "q"}


@pytest.mark.parametrize(
    ("exc_type", "status_code", "code"),
    [
        (ConfigurationError, 500, "configuration_error"),
        (ToolExecutionError, 500, "tool_execution_error"),
        (RetrievalError, 500, "retrieval_error"),
        (NotFoundError, 404, "not_found"),
        (AuthenticationError, 401, "authentication_error"),
        (AuthorizationError, 403, "authorization_error"),
    ],
)
def test_subclasses_are_importable_with_defaults(exc_type, status_code, code) -> None:
    exc = exc_type("message")

    assert isinstance(exc, AppException)
    assert exc.status_code == status_code
    assert exc.code == code


def test_integration_error_records_upstream_details() -> None:
    exc = IntegrationError("Jira down", integration="jira", upstream_status=503)

    assert exc.status_code == 502
    assert exc.code == "integration_error"
    assert exc.integration == "jira"
    assert exc.upstream_status == 503


def test_subclass_defaults_do_not_leak_between_instances() -> None:
    AppException("custom", 418)

    assert AppException("again").status_code == 500
