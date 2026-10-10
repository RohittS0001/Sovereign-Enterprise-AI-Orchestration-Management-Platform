
from typing import Any

from backend.app.integrations.email.client import GmailClient


class EmailService:
    """Business logic for Gmail operations."""

    def __init__(self, client: GmailClient | None = None) -> None:
        self.client = client or GmailClient()

    def get_profile(self) -> dict[str, Any]:
        """Return the authenticated Gmail account profile."""
        return self.client.get_profile()

    def search_messages(
        self,
        query: str = "",
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Search Gmail messages and return the API results."""
        return self.client.search_messages(
            query=query,
            max_results=max_results,
            page_token=page_token,
        )

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Retrieve a Gmail message by its ID."""
        return self.client.get_message(message_id)
