
from unittest.mock import MagicMock

import pytest

from backend.app.integrations.email.service import EmailService


@pytest.fixture
def mock_gmail_client():
    return MagicMock()


@pytest.fixture
def email_service(mock_gmail_client):
    return EmailService(client=mock_gmail_client)


def test_get_profile(email_service, mock_gmail_client):
    expected = {"emailAddress": "test@example.com"}
    mock_gmail_client.get_profile.return_value = expected

    result = email_service.get_profile()

    assert result == expected
    mock_gmail_client.get_profile.assert_called_once_with()


def test_search_messages(email_service, mock_gmail_client):
    expected = {"messages": [{"id": "message-1"}]}
    mock_gmail_client.search_messages.return_value = expected

    result = email_service.search_messages(
        query="is:unread",
        max_results=5,
        page_token="next-page",
    )

    assert result == expected
    mock_gmail_client.search_messages.assert_called_once_with(
        query="is:unread",
        max_results=5,
        page_token="next-page",
    )


def test_get_message(email_service, mock_gmail_client):
    expected = {"id": "message-1", "snippet": "Hello"}
    mock_gmail_client.get_message.return_value = expected

    result = email_service.get_message("message-1")

    assert result == expected
    mock_gmail_client.get_message.assert_called_once_with("message-1")
