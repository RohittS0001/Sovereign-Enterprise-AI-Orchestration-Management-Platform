"""API schemas for the audit log."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
