import pytest
from pydantic import ValidationError

from backend.app.mcp.schemas.email import (
    GetEmailInput,
    SearchEmailsInput,
)


def test_search_emails_input_defaults() -> None:
    result = SearchEmailsInput()

    assert result.query == ""
    assert result.max_results == 10
    assert result.page_token is None


@pytest.mark.parametrize("max_results", [0, 101])
def test_search_emails_rejects_invalid_limit(max_results: int) -> None:
    with pytest.raises(ValidationError):
        SearchEmailsInput(max_results=max_results)


def test_get_email_input_rejects_empty_message_id() -> None:
    with pytest.raises(ValidationError):
        GetEmailInput(message_id="")


def test_get_email_input_accepts_valid_message_id() -> None:
    result = GetEmailInput(message_id="message-123")

    assert result.message_id == "message-123"
