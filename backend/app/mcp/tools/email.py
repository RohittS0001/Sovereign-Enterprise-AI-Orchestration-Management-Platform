from typing import Any

from pydantic import ValidationError

from backend.app.integrations.email.service import EmailService
from backend.app.mcp.schemas.common import ToolResult, ToolStatus
from backend.app.mcp.schemas.email import (
    EmailMessageSummary,
    EmailProfileOutput,
    GetEmailInput,
    GetEmailOutput,
    SearchEmailsInput,
    SearchEmailsOutput,
)


class EmailTools:
    """MCP-facing tools for Gmail operations."""

    def __init__(self, service: EmailService | None = None) -> None:
        self.service = service or EmailService()

    def get_profile(self) -> ToolResult:
        """Return the authenticated Gmail account profile."""
        try:
            profile = self.service.get_profile()

            output = EmailProfileOutput(
                email_address=profile.get("emailAddress", ""),
                messages_total=profile.get("messagesTotal", 0),
                threads_total=profile.get("threadsTotal", 0),
            )

            return ToolResult(
                status=ToolStatus.SUCCESS,
                data=output.model_dump(),
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Failed to retrieve Gmail profile: {exc}",
            )

    def search_emails(self, payload: dict[str, Any]) -> ToolResult:
        """Search Gmail messages and return validated message summaries."""
        try:
            inputs = SearchEmailsInput.model_validate(payload)
            result = self.service.search_messages(
                query=inputs.query,
                max_results=inputs.max_results,
                page_token=inputs.page_token,
            )

            messages = [
                EmailMessageSummary(
                    message_id=message["id"],
                    thread_id=message.get("threadId"),
                )
                for message in result.get("messages", [])
                if message.get("id")
            ]

            output = SearchEmailsOutput(
                messages=messages,
                next_page_token=result.get("nextPageToken"),
                result_size_estimate=result.get("resultSizeEstimate", 0),
            )

            return ToolResult(
                status=ToolStatus.SUCCESS,
                data=output.model_dump(),
            )
        except (ValidationError, ValueError) as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Invalid email search input: {exc}",
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Failed to search Gmail messages: {exc}",
            )

    def get_email(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve a Gmail message by its ID."""
        try:
            inputs = GetEmailInput.model_validate(payload)
            message = self.service.get_message(inputs.message_id)

            output = GetEmailOutput(
                message_id=message.get("id", inputs.message_id),
                thread_id=message.get("threadId"),
                snippet=message.get("snippet", ""),
                body=self._extract_body(message.get("payload", {})),
            )

            return ToolResult(
                status=ToolStatus.SUCCESS,
                data=output.model_dump(),
            )
        except (ValidationError, ValueError) as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Invalid email retrieval input: {exc}",
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Failed to retrieve Gmail message: {exc}",
            )

    @staticmethod
    def _extract_body(payload: dict[str, Any]) -> str:
        """Extract decoded text from a Gmail message payload."""
        parts = payload.get("parts", [])

        if parts:
            for part in parts:
                body = EmailTools._extract_body(part)
                if body:
                    return body

        body_data = payload.get("body", {}).get("data")
        if body_data:
            from backend.app.integrations.email.client import GmailClient

            return GmailClient.decode_body(body_data)

        return ""
