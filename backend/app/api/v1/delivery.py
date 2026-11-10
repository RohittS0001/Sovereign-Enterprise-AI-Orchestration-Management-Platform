"""Delivery lifecycle: roadmap + critical path, DORA metrics, release notes, retrospectives
and requirements → backlog drafting (submitted to Jira only through approvals)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import SessionDep, SettingsDep, ToolRegistryDep, require_permissions
from backend.app.api.v1.projects import KeyPath
from backend.app.core.errors import ValidationAppError
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.analytics.service import get_project, project_health
from backend.app.domains.delivery.backlog import BacklogProposal, draft_backlog, submit_backlog
from backend.app.domains.delivery.dora import DoraReport, dora_metrics, incident_history
from backend.app.domains.delivery.release import (
    ReleaseNotes,
    Retrospective,
    release_notes,
    retrospective,
)
from backend.app.domains.delivery.roadmap import Roadmap, build_roadmap
from backend.app.tools.gateway import ToolGateway

router = APIRouter(prefix="/api/v1/projects", tags=["Delivery lifecycle"])

Analyst = Annotated[Principal, Depends(require_permissions(Permission.ANALYTICS_READ))]
Planner = Annotated[
    Principal, Depends(require_permissions(Permission.AGENT_QUERY, Permission.ANALYTICS_READ))
]


async def _snapshot(request: Request, session, key: str):
    project = await get_project(session, key)
    return await request.app.state.analytics_collector.collect(project)


def _reporting_model(request: Request):
    models = request.app.state.agent_models
    return models.reporting if models else None


@router.get("/{key}/roadmap", response_model=Roadmap)
async def roadmap(
    key: KeyPath, principal: Analyst, session: SessionDep, request: Request
) -> Roadmap:
    """Dependency graph, resource-aware schedule and the critical path setting the finish date."""
    return build_roadmap(await _snapshot(request, session, key))


@router.get("/{key}/dora", response_model=DoraReport)
async def dora(
    key: KeyPath,
    principal: Analyst,
    session: SessionDep,
    request: Request,
    window_days: Annotated[int, Query(ge=7, le=180)] = 30,
) -> DoraReport:
    """Deployment frequency, lead time, change failure rate and time to restore, banded."""
    snapshot = await _snapshot(request, session, key)
    incidents = await incident_history(request.app.state.analytics_collector.hub, snapshot)
    return dora_metrics(snapshot, incidents, window_days=window_days)


@router.get("/{key}/release-notes", response_model=ReleaseNotes)
async def notes(
    key: KeyPath,
    principal: Analyst,
    session: SessionDep,
    request: Request,
    since: date | None = None,
    until: date | None = None,
) -> ReleaseNotes:
    """Release notes from completed issues and merged pull requests (default: this sprint)."""
    if since and until and since > until:
        raise ValidationAppError("'since' must be on or before 'until'")
    return release_notes(await _snapshot(request, session, key), since, until)


@router.get("/{key}/retrospective", response_model=Retrospective)
async def retro(
    key: KeyPath, principal: Analyst, session: SessionDep, request: Request
) -> Retrospective:
    """Went well / to improve / actions for the sprint, every point traced to data."""
    collector = request.app.state.analytics_collector
    health = await project_health(session, collector, key, record=False)
    snapshot = await _snapshot(request, session, key)
    return await retrospective(snapshot, health, _reporting_model(request))


class DraftRequest(BaseModel):
    requirements: str = Field(min_length=20, max_length=20_000)


@router.post("/{key}/backlog/draft", response_model=BacklogProposal)
async def draft(
    key: KeyPath, body: DraftRequest, principal: Planner, session: SessionDep, request: Request
) -> BacklogProposal:
    """Epics and stories with acceptance criteria and history-based estimates (not saved)."""
    snapshot = await _snapshot(request, session, key)
    return await draft_backlog(body.requirements, snapshot, _reporting_model(request))


@router.post("/{key}/backlog/submit")
async def submit(
    key: KeyPath,
    body: BacklogProposal,
    principal: Planner,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
) -> list[dict]:
    """Send the reviewed backlog to Jira: each epic and story waits for a human approval."""
    await get_project(session, key)
    if not any(e.stories for e in body.epics):
        raise ValidationAppError("The backlog has no stories to submit")
    if sum(len(e.stories) + 1 for e in body.epics) > 60:
        raise ValidationAppError("Submit at most 60 items at a time")
    return await submit_backlog(
        ToolGateway(registry, session, settings), principal, key.upper(), body
    )
