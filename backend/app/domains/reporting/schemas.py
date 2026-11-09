"""API schemas for reports."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ReportGenerate(BaseModel):
    project_key: str = Field(min_length=1, max_length=32)


class ReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_key: str
    kind: str
    trigger: str
    period_start: date
    period_end: date
    risk_score: int
    risk_level: str
    generated_by_model: str | None
    created_at: datetime


class ReportRead(ReportSummary):
    content_markdown: str
    metrics: dict[str, Any]


class ReportPage(BaseModel):
    items: list[ReportSummary]
    total: int
    limit: int
    offset: int


class ReportEmail(BaseModel):
    to: list[EmailStr] = Field(default_factory=list, max_length=20)
