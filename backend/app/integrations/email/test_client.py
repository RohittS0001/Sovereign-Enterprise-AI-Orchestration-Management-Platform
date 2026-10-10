from unittest.mock import MagicMock

import pytest

from backend.app.integrations.email.client import GmailClient


@pytest.fixture
def mock_gmail_service():
    return MagicMock()


@pytest.fixture
def gmail_client(mock_gmail_service):
    return GmailClient(service=mock_gmail_service)


def test_get_profile(gmail_client, mock_gmail_service):
    expected_profile = {
        "emailAddress": "rohittsasane@gmail.com",
        "messagesTotal": 100,
        "threadsTotal": 80,
        "historyId": "12345",
    }

    mock_gmail_service.users().getProfile().execute.return_value = (
        expected_profile
    )

    result = gmail_client.get_profile()

    assert result == expected_profile
    mock_gmail_service.users().getProfile.assert_any_call(
    userId="me"
    )


def test_search_messages(gmail_client, mock_gmail_service):
    expected_messages = {
        "messages": [
            {"id": "message-1", "threadId": "thread-1"},
            {"id": "message-2", "threadId": "thread-2"},
        ],
        "resultSizeEstimate": 2,
    }

    mock_gmail_service.users().messages().list().execute.return_value = (
        expected_messages
    )

    result = gmail_client.search_messages(
        query="is:unread",
        max_results=10,
    )

    mock_gmail_service.users().messages().list.assert_any_call(
    userId="me",
    q="is:unread",
    maxResults=10,
    )


def test_search_messages_rejects_invalid_max_results(gmail_client):
    with pytest.raises(ValueError, match="max_results must be between 1 and 100"):
        gmail_client.search_messages(max_results=0)


def test_get_message(gmail_client, mock_gmail_service):
    expected_message = {
        "id": "message-1",
        "threadId": "thread-1",
        "snippet": "Hello from Gmail",
        "payload": {"headers": []},
    }

    mock_gmail_service.users().messages().get().execute.return_value = (
        expected_message
    )

    result = gmail_client.get_message("message-1")

    assert result == expected_message
    mock_gmail_service.users().messages().get.assert_any_call(
    userId="me",
    id="message-1",
    format="full",
    )


def test_get_message_rejects_empty_id(gmail_client):
    with pytest.raises(ValueError, match="message_id must not be empty"):
        gmail_client.get_message("   ")


def test_decode_body():
    import base64

    encoded_body = base64.urlsafe_b64encode(
        b"Hello from Gmail"
    ).decode("utf-8").rstrip("=")

    assert GmailClient.decode_body(encoded_body) == "Hello from Gmail"


def test_decode_body_returns_empty_string_for_missing_data():
    assert GmailClient.decode_body(None) == ""
    assert GmailClient.decode_body("") == ""