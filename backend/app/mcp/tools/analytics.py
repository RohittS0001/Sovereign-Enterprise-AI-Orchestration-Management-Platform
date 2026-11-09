from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from backend.app.analytics.collector import SnapshotCollector
from backend.app.analytics.service import project_health
from backend.app.core.exceptions.exceptions import AppException
from backend.app.db.models import Project
from backend.app.db.session import Database
from backend.app.integrations.jira.validators import ProjectKey
from backend.app.mcp.schemas.common import ToolResult, ToolStatus


class ProjectHealthInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")


class ListProjectsInput(BaseModel):
    pass


class AnalyticsTools:
    """Computed, evidence-backed project analytics for the agent."""

    def __init__(self, database: Database, collector: SnapshotCollector) -> None:
        self.database = database
        self.collector = collector

    async def list_projects(self, payload: dict[str, Any]) -> ToolResult:
        async with self.database.session() as session:
            projects = await session.scalars(select(Project).where(Project.is_active.is_(True)))
            data = [{"key": p.key, "name": p.name, "repos": p.github_repos} for p in projects]
        return ToolResult(status=ToolStatus.SUCCESS, data={"projects": data})

    async def project_health(self, payload: dict[str, Any]) -> ToolResult:
        try:
            inputs = ProjectHealthInput.model_validate(payload)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        try:
            async with self.database.session() as session:
                health = await project_health(session, self.collector, inputs.project_key)
        except AppException as exc:
            return ToolResult(status=ToolStatus.ERROR, error=exc.message)
        # Drop zero-score factors and per-issue lists the model does not need.
        data = health.model_dump(mode="json")
        data["risk"]["factors"] = [f for f in data["risk"]["factors"] if f["score"] > 0]
        return ToolResult(status=ToolStatus.SUCCESS, data=data)
