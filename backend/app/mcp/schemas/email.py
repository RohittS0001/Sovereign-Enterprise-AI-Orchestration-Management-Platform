from pydantic import BaseModel, Field


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


class GetEmailOutput(BaseModel):
    """Output containing the retrieved Gmail message."""

    message_id: str
    thread_id: str | None = None
    snippet: str = ""
    body: str = ""
