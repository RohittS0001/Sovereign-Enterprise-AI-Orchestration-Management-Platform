from pydantic import BaseModel, EmailStr, Field, field_validator


class SearchEmailsInput(BaseModel):
    """Input for searching Gmail messages."""

    query: str = ""
    max_results: int = Field(default=10, ge=1, le=100)
    page_token: str | None = None


class GetEmailInput(BaseModel):
    """Input for retrieving a Gmail message."""

    message_id: str = Field(min_length=1)


class EmailProfileOutput(BaseModel):
    """Authenticated Gmail profile information."""

    email_address: str
    messages_total: int
    threads_total: int


class EmailMessageSummary(BaseModel):
    """Summary of a Gmail message returned by a search."""

    message_id: str = Field(min_length=1)
    thread_id: str | None = None


class SearchEmailsOutput(BaseModel):
    """Output from searching Gmail messages."""

    messages: list[EmailMessageSummary] = Field(default_factory=list)
    next_page_token: str | None = None
    result_size_estimate: int = 0


class CreateDraftInput(BaseModel):
    """Input for drafting (not sending) an email."""

    to: list[EmailStr] = Field(min_length=1, max_length=20)
    cc: list[EmailStr] = Field(default_factory=list, max_length=20)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    thread_id: str | None = Field(default=None, description="Reply within this Gmail thread")

    @field_validator("subject")
    @classmethod
    def _single_line(cls, value: str) -> str:
        # Newlines in a header would allow header injection.
        if any(ch in value for ch in (chr(13), chr(10))):
            raise ValueError("subject must be a single line")
        return value


class GetEmailOutput(BaseModel):
    """Output containing the retrieved Gmail message."""

    message_id: str
    thread_id: str | None = None
    snippet: str = ""
    body: str = ""
