"""API schemas for approval requests."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domains.governance.approvals.models import ApprovalStatus


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
