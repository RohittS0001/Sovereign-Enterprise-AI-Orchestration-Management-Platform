from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolOperation(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class ToolResult(BaseModel):
    status: ToolStatus
    data: Any = None
    error: str | None = None