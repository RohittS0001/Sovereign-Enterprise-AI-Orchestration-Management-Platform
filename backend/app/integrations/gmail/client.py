import base64
from email.message import EmailMessage
from typing import Any

from googleapiclient.discovery import build

from backend.app.core.config import Settings
from backend.app.integrations.gmail.auth import load_gmail_credentials


class GmailClient:
    """Low-level client for the Gmail API.

    The Google API client is synchronous; async callers should run these methods in a
    worker thread (see ``EmailTools``) so they do not block the event loop.
    """

    def __init__(self, service: Any = None, settings: Settings | None = None) -> None:
        # Built on first use so constructing the client never triggers credential loading.
        self._service_instance = service
        self._settings = settings

    @property
    def _service(self) -> Any:
        if self._service_instance is None:
            self._service_instance = build(
                "gmail",
                "v1",
                credentials=load_gmail_credentials(self._settings),
                cache_discovery=False,
            )
        return self._service_instance

    def get_profile(self) -> dict[str, Any]:
        """Return the authenticated Gmail profile."""
        return self._service.users().getProfile(userId="me").execute()

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

        return self._service.users().messages().list(**params).execute()

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Retrieve a Gmail message, including its headers and body."""
        if not message_id.strip():
            raise ValueError("message_id must not be empty")

        return (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def create_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Create (not send) a plain-text draft in the authenticated mailbox."""
        message = EmailMessage()
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message.set_content(body)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        draft_message: dict[str, Any] = {"raw": raw}
        if thread_id:
            draft_message["threadId"] = thread_id

        return (
            self._service.users()
            .drafts()
            .create(userId="me", body={"message": draft_message})
            .execute()
        )

    @staticmethod
    def decode_body(data: str | None) -> str:
        """Decode a Gmail base64url-encoded message body."""
        if not data:
            return ""

        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

        return decoded.decode("utf-8", errors="replace")
