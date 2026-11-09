import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.app.db.models import RunStatus


class AgentQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)

    def normalized_query(self) -> str:
        return self.query.strip()


class AgentStep(BaseModel):
    id: str = Field(validation_alias="step_id")
    tool: str
    purpose: str
    status: str
    error: str | None = None
    approval_id: str | None = None
    suspicious: bool = False


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class AgentRunResponse(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    query: str
    answer: str | None
    citations: list[str]
    steps: list[AgentStep]
    pending_approvals: list[str]
    flagged_injection: bool
    error: str | None
    usage: TokenUsage
    started_at: datetime
    finished_at: datetime | None


class AgentRunPage(BaseModel):
    items: list[AgentRunResponse]
    total: int
    limit: int
    offset: int


class AgentToolInfo(BaseModel):
    name: str
    description: str
    operation: str
    requires_approval: bool
    input_schema: dict[str, Any]
