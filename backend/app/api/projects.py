from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy import select

from backend.app.analytics.metrics.results import ProjectHealth
from backend.app.analytics.service import get_project, project_health
from backend.app.api.deps import SessionDep, require_permissions
from backend.app.audit import actions
from backend.app.audit.service import AuditService
from backend.app.core.security.permissions import Permission, Principal
from backend.app.db.models import Project
from backend.app.integrations.jira.validators import PROJECT_KEY_PATTERN
from backend.app.schemas.projects import ProjectRead, ProjectUpsert

router = APIRouter(prefix="/api/v1/projects", tags=["Projects & analytics"])

Reader = Annotated[Principal, Depends(require_permissions(Permission.PROJECTS_READ))]
Writer = Annotated[Principal, Depends(require_permissions(Permission.PROJECTS_WRITE))]
Analyst = Annotated[Principal, Depends(require_permissions(Permission.ANALYTICS_READ))]
KeyPath = Annotated[str, Path(pattern=PROJECT_KEY_PATTERN, description="Jira project key")]


@router.get("", response_model=list[ProjectRead])
async def list_projects(principal: Reader, session: SessionDep) -> list[ProjectRead]:
    projects = await session.scalars(select(Project).order_by(Project.key))
    return [ProjectRead.model_validate(p) for p in projects]


@router.put("/{key}", response_model=ProjectRead)
async def upsert_project(
    key: KeyPath, body: ProjectUpsert, principal: Writer, session: SessionDep, request: Request
) -> ProjectRead:
    """Create or update the analytics configuration for a Jira project."""
    key = key.upper()
    project = await session.scalar(select(Project).where(Project.key == key))
    if project is None:
        project = Project(key=key)
        session.add(project)
    data = body.model_dump(mode="json")
    for field, value in data.items():
        setattr(project, field, value)
    await session.flush()
    await AuditService(session).record(
        actions.PROJECT_UPSERT,
        status=actions.SUCCESS,
        principal=principal,
        resource_type="project",
        resource_id=key,
        details={"repos": body.github_repos, "members": len(body.members)},
    )
    await session.commit()
    request.app.state.analytics_collector._cache.pop(key, None)
    return ProjectRead.model_validate(project)


@router.get("/{key}", response_model=ProjectRead)
async def read_project(key: KeyPath, principal: Reader, session: SessionDep) -> ProjectRead:
    return ProjectRead.model_validate(await get_project(session, key))


@router.get("/{key}/health", response_model=ProjectHealth)
async def health(
    key: KeyPath, request: Request, principal: Analyst, session: SessionDep, fresh: bool = False
) -> ProjectHealth:
    """Risk score with evidence, sprint forecast, workload/capacity and Jira↔GitHub checks."""
    return await project_health(session, request.app.state.analytics_collector, key, fresh=fresh)
