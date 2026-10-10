
import base64
from typing import Any
from googleapiclient.discovery import build

from backend.app.integrations.email.auth import get_gmail_credentials


class GmailClient:
    """Low-level client for the Gmail API."""

    def __init__(self, service: Any = None) -> None:
        self._service = service or build(
            "gmail",
            "v1",
            credentials=get_gmail_credentials(),
            cache_discovery=False,
        )

    def get_profile(self) -> dict[str, Any]:
        """Return the authenticated Gmail profile."""
        return (
            self._service.users()
            .getProfile(userId="me")
            .execute()
        )

    def search_messages(
        self,
        query: str = "",
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Search Gmail messages using Gmail search syntax."""
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")

        params: dict[str, Any] = {
            "userId": "me",
            "q": query,
            "maxResults": max_results,
        }

        if page_token:
            params["pageToken"] = page_token

        return (
            self._service.users()
            .messages()
            .list(**params)
            .execute()
        )

    def get_message(
        self,
        message_id: str,
    ) -> dict[str, Any]:
        """Retrieve a Gmail message, including its headers and body."""
        if not message_id.strip():
            raise ValueError("message_id must not be empty")

        return (
            self._service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
            .execute()
        )

    @staticmethod
    def decode_body(data: str | None) -> str:
        """Decode a Gmail base64url-encoded message body."""
        if not data:
            return ""

        decoded = base64.urlsafe_b64decode(
            data + "=" * (-len(data) % 4)
        )

        return decoded.decode("utf-8", errors="replace")
