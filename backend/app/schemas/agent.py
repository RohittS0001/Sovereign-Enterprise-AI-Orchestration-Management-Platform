import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.db.models import ApprovalStatus, RunStatus


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


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID | None
    requested_by_id: uuid.UUID
    tool_name: str
    permission: str
    arguments: dict[str, Any]
    summary: str
    risk_flags: list[str]
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    decided_by_id: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    result: dict[str, Any] | None
    error: str | None


class ApprovalPage(BaseModel):
    items: list[ApprovalRead]
    total: int
    limit: int
    offset: int


class ApprovalDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    status: str
    request_id: str | None
    run_id: uuid.UUID | None
    details: dict[str, Any]
    hash: str


class AuditPage(BaseModel):
    items: list[AuditEventRead]
    total: int
    limit: int
    offset: int


class AuditVerification(BaseModel):
    valid: bool
    checked: int
    first_invalid_seq: int | None
    reason: str | None
