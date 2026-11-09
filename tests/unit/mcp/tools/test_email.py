from unittest.mock import MagicMock

import pytest

from backend.app.mcp.schemas.common import ToolStatus
from backend.app.mcp.tools.email import EmailTools


@pytest.fixture
def mock_service():
    return MagicMock()


@pytest.fixture
def email_tools(mock_service):
    return EmailTools(service=mock_service)


@pytest.mark.asyncio
async def test_get_profile(email_tools, mock_service):
    mock_service.get_profile.return_value = {
        "emailAddress": "test@example.com",
        "messagesTotal": 25,
        "threadsTotal": 20,
    }

    result = await email_tools.get_profile()

    assert result.status == ToolStatus.SUCCESS
    assert result.data["email_address"] == "test@example.com"
    assert result.data["messages_total"] == 25
    mock_service.get_profile.assert_called_once_with()


@pytest.mark.asyncio
async def test_search_emails(email_tools, mock_service):
    mock_service.search_messages.return_value = {
        "messages": [
            {"id": "message-1", "threadId": "thread-1"},
            {"id": "message-2", "threadId": "thread-2"},
        ],
        "nextPageToken": "next-page",
        "resultSizeEstimate": 15,
    }

    result = await email_tools.search_emails({"query": "is:unread", "max_results": 5})

    assert result.status == ToolStatus.SUCCESS
    assert len(result.data["messages"]) == 2
    assert result.data["messages"][0]["message_id"] == "message-1"
    assert result.data["next_page_token"] == "next-page"
    mock_service.search_messages.assert_called_once_with(
        query="is:unread",
        max_results=5,
        page_token=None,
    )


@pytest.mark.asyncio
async def test_search_emails_rejects_invalid_limit(email_tools):
    result = await email_tools.search_emails({"max_results": 101})

    assert result.status == ToolStatus.ERROR
    assert "Invalid email search input" in result.error


@pytest.mark.asyncio
async def test_get_email(email_tools, mock_service):
    mock_service.get_message.return_value = {
        "id": "message-1",
        "threadId": "thread-1",
        "snippet": "Hello there",
        "payload": {
            "body": {
                "data": "SGVsbG8gd29ybGQ",
            }
        },
    }

    result = await email_tools.get_email({"message_id": "message-1"})

    assert result.status == ToolStatus.SUCCESS
    assert result.data["message_id"] == "message-1"
    assert result.data["snippet"] == "Hello there"
    assert result.data["body"] == "Hello world"
    mock_service.get_message.assert_called_once_with("message-1")


@pytest.mark.asyncio
async def test_get_email_rejects_empty_id(email_tools):
    result = await email_tools.get_email({"message_id": ""})

    assert result.status == ToolStatus.ERROR
    assert "Invalid email retrieval input" in result.error


@pytest.mark.asyncio
async def test_get_email_handles_service_error(email_tools, mock_service):
    mock_service.get_message.side_effect = RuntimeError("Gmail unavailable")

    result = await email_tools.get_email({"message_id": "message-1"})

    assert result.status == ToolStatus.ERROR
    assert "Failed to retrieve Gmail message" in result.error


@pytest.mark.asyncio
async def test_get_profile_handles_service_error(email_tools, mock_service):
    mock_service.get_profile.side_effect = RuntimeError("Gmail unavailable")

    result = await email_tools.get_profile()

    assert result.status == ToolStatus.ERROR
    assert "Failed to retrieve Gmail profile" in result.error


@pytest.mark.asyncio
async def test_get_email_extracts_nested_multipart_body(email_tools, mock_service):
    mock_service.get_message.return_value = {
        "id": "message-2",
        "payload": {
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": "SGVsbG8gd29ybGQ"}},
                    ],
                },
            ],
        },
    }

    result = await email_tools.get_email({"message_id": "message-2"})

    assert result.status == ToolStatus.SUCCESS
    assert result.data["body"] == "Hello world"


def test_constructing_tools_does_not_load_credentials():
    # Building the tools (and the default service/client) must not touch Gmail auth.
    tools = EmailTools()

    assert tools.service._client is None
