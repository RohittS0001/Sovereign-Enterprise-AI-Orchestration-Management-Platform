import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.app.integrations.github.service import validate_ref, validate_repository


class ProjectMember(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jira_account_id: str | None = Field(default=None, max_length=128)
    keka_employee_id: str | None = Field(default=None, max_length=64)
    capacity_points: float = Field(default=10.0, gt=0, le=200, description="Points per sprint")


class ProjectUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    github_repos: list[str] = Field(default_factory=list, max_length=20)
    default_branch: str = "main"
    active_sprint_id: int | None = Field(default=None, gt=0)
    members: list[ProjectMember] = Field(default_factory=list, max_length=100)
    report_recipients: list[EmailStr] = Field(default_factory=list, max_length=50)
    is_active: bool = True

    @field_validator("github_repos")
    @classmethod
    def _repos(cls, repos: list[str]) -> list[str]:
        for repo in repos:
            validate_repository(repo, [])
        return repos

    @field_validator("default_branch")
    @classmethod
    def _branch(cls, branch: str) -> str:
        return validate_ref(branch)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    github_repos: list[str]
    default_branch: str
    active_sprint_id: int | None
    members: list[dict[str, Any]]
    report_recipients: list[str]
    is_active: bool
    updated_at: datetime
