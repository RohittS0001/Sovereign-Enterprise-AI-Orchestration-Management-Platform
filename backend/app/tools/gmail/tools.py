import asyncio
from typing import Any

from pydantic import ValidationError

from backend.app.integrations.gmail.client import GmailClient
from backend.app.integrations.gmail.service import EmailService
from backend.app.tools.base import ToolResult, ToolStatus
from backend.app.tools.gmail.schemas import (
    CreateDraftInput,
    EmailMessageSummary,
    EmailProfileOutput,
    GetEmailInput,
    GetEmailOutput,
    SearchEmailsInput,
    SearchEmailsOutput,
)


class EmailTools:
    """MCP-facing tools for Gmail operations.

    The Gmail SDK is blocking, so each call runs in a worker thread to keep the
    event loop responsive.
    """

    def __init__(
        self,
        service: EmailService | None = None,
        allowed_recipient_domains: list[str] | None = None,
    ) -> None:
        self.service = service or EmailService()
        self.allowed_domains = {d.lower().lstrip("@") for d in allowed_recipient_domains or []}

    async def create_draft(self, payload: dict[str, Any]) -> ToolResult:
        """Draft an email for a person to review and send from Gmail."""
        try:
            inputs = CreateDraftInput.model_validate(payload)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid draft input: {exc}")

        recipients = [*inputs.to, *inputs.cc]
        if self.allowed_domains:
            blocked = sorted(
                r for r in recipients if r.rsplit("@", 1)[-1].lower() not in self.allowed_domains
            )
            if blocked:
                return ToolResult(
                    status=ToolStatus.ERROR,
                    error=f"Recipients outside allowed domains: {', '.join(blocked)}",
                )

        try:
            draft = await asyncio.to_thread(
                self.service.create_draft,
                to=list(inputs.to),
                cc=list(inputs.cc),
                subject=inputs.subject,
                body=inputs.body,
                thread_id=inputs.thread_id,
            )
        except Exception as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Failed to create Gmail draft: {exc}")

        return ToolResult(
            status=ToolStatus.SUCCESS,
            data={
                "draft_id": draft.get("id"),
                "message_id": (draft.get("message") or {}).get("id"),
                "to": recipients,
                "subject": inputs.subject,
                "status": "draft_created",
            },
        )

    async def get_profile(self, payload: dict[str, Any] | None = None) -> ToolResult:
        """Return the authenticated Gmail account profile."""
        try:
            profile = await asyncio.to_thread(self.service.get_profile)

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

    async def search_emails(self, payload: dict[str, Any]) -> ToolResult:
        """Search Gmail messages and return validated message summaries."""
        try:
            inputs = SearchEmailsInput.model_validate(payload)
            result = await asyncio.to_thread(
                self.service.search_messages,
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

    async def get_email(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve a Gmail message by its ID."""
        try:
            inputs = GetEmailInput.model_validate(payload)
            message = await asyncio.to_thread(self.service.get_message, inputs.message_id)

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
            return GmailClient.decode_body(body_data)

        return ""
