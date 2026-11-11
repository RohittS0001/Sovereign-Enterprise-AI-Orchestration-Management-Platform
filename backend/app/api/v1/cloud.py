"""Cloud monitoring: one project's resources, cost, health and security, and the portfolio."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from backend.app.api.deps import SessionDep, require_permissions
from backend.app.api.v1.projects import KeyPath
from backend.app.core.security.permissions import Permission, Principal
from backend.app.db.models import Project
from backend.app.domains.analytics.service import get_project
from backend.app.domains.cloud.service import Portfolio, ProjectCloud, portfolio, project_cloud

router = APIRouter(prefix="/api/v1", tags=["Cloud monitoring"])

Analyst = Annotated[Principal, Depends(require_permissions(Permission.ANALYTICS_READ))]


@router.get("/projects/{key}/cloud", response_model=ProjectCloud)
async def cloud(
    key: KeyPath, principal: Analyst, session: SessionDep, request: Request
) -> ProjectCloud:
    """Everything the project runs on AWS / Azure: where and why, health, storage, cost,
    alarms and (for admins and auditors) security findings."""
    project = await get_project(session, key)
    return await project_cloud(
        request.app.state.integrations,
        project,
        date.today(),
        include_security=principal.has(Permission.AUDIT_READ),
    )


@router.get("/portfolio", response_model=Portfolio)
async def all_projects(principal: Analyst, session: SessionDep, request: Request) -> Portfolio:
    """Every active project side by side: team, cloud resources, health, storage and cost."""
    projects = list(
        await session.scalars(
            select(Project).where(Project.is_active.is_(True)).order_by(Project.key)
        )
    )
    return await portfolio(
        request.app.state.integrations,
        projects,
        date.today(),
        include_security=principal.has(Permission.AUDIT_READ),
    )
