import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.app.integrations.connectors.base import safe_id
from backend.app.integrations.github.service import validate_ref, validate_repository

# Project.links keys: which records in other systems belong to this project.
LINK_KEYS = {
    "pagerduty_services": "PagerDuty service ids", "opsgenie_tags": "Opsgenie alert tags",
    "sentry_projects": "Sentry project slugs", "jenkins_jobs": "Jenkins job names",
    "argocd_apps": "Argo CD application names", "sonarqube_projects": "SonarQube project keys",
    "cost_centers": "Finance project / cost-centre names (budget vs actuals)",
}  # fmt: skip


class ProjectMember(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jira_account_id: str | None = Field(default=None, max_length=128)
    keka_employee_id: str | None = Field(default=None, max_length=64)
    hr_employee_id: str | None = Field(
        default=None, max_length=64, description="Id in another HR system"
    )
    capacity_points: float = Field(default=10.0, gt=0, le=200, description="Points per sprint")


class ProjectUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    github_repos: list[str] = Field(default_factory=list, max_length=20)
    default_branch: str = "main"
    active_sprint_id: int | None = Field(default=None, gt=0)
    members: list[ProjectMember] = Field(default_factory=list, max_length=100)
    report_recipients: list[EmailStr] = Field(default_factory=list, max_length=50)
    links: dict[str, list[str]] = Field(default_factory=dict, description=str(LINK_KEYS))
    is_active: bool = True

    @field_validator("links")
    @classmethod
    def _links(cls, links: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = set(links) - set(LINK_KEYS)
        if unknown:
            raise ValueError(f"unknown link keys {sorted(unknown)}; allowed: {sorted(LINK_KEYS)}")
        for values in links.values():
            if len(values) > 20:
                raise ValueError("at most 20 values per link")
            for value in values:
                safe_id(value, "link value")
        return links

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
    links: dict[str, list[str]] = Field(default_factory=dict)
    is_active: bool
    updated_at: datetime
