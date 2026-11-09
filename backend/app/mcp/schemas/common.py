from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolOperation(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"


class ToolResult(BaseModel):
    status: ToolStatus
    data: Any = None
    error: str | None = None
    # True when retrying the same call may succeed (timeouts, 429, 5xx).
    retryable: bool = False
    approval_id: str | None = None
