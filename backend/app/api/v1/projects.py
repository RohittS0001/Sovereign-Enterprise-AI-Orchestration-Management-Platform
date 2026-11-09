import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.api.deps import SessionDep, require_permissions
from backend.app.core.security.permissions import Permission, Principal
from backend.app.db.models import Project
from backend.app.domains.analytics.negotiation import NegotiationPlan, negotiate
from backend.app.domains.analytics.results import ProjectHealth
from backend.app.domains.analytics.schemas import ProjectRead, ProjectUpsert
from backend.app.domains.analytics.service import get_project, project_health
from backend.app.domains.analytics.twin import Scenario, SimulationResult, simulate
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.integrations.jira.validators import PROJECT_KEY_PATTERN

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


class SimulateRequest(BaseModel):
    scenarios: list[Scenario] = Field(default_factory=list, max_length=6)
    target_date: date | None = Field(default=None, description="Default: the sprint end")
    trials: int = Field(default=1000, ge=100, le=5000)


@router.post("/{key}/simulate", response_model=list[SimulationResult])
async def simulate_project(
    key: KeyPath, body: SimulateRequest, principal: Analyst, session: SessionDep, request: Request
) -> list[SimulationResult]:
    """Monte Carlo delivery forecast: the baseline first, then each what-if scenario."""
    project = await get_project(session, key)
    snapshot = await request.app.state.analytics_collector.collect(project)
    runs = [Scenario(target_date=body.target_date)] + [
        s if s.target_date else s.model_copy(update={"target_date": body.target_date})
        for s in body.scenarios
    ]
    return await asyncio.to_thread(
        lambda: [simulate(snapshot, s, trials=body.trials) for s in runs]
    )


class NegotiateRequest(BaseModel):
    target_date: date | None = None


@router.post("/{key}/negotiate", response_model=NegotiationPlan)
async def negotiate_project(
    key: KeyPath, body: NegotiateRequest, principal: Analyst, session: SessionDep, request: Request
) -> NegotiationPlan:
    """Options for an at-risk deadline, each simulated, plus a drafted stakeholder note."""
    project = await get_project(session, key)
    snapshot = await request.app.state.analytics_collector.collect(project)
    models = request.app.state.agent_models
    return await negotiate(snapshot, body.target_date, model=models.reporting if models else None)
