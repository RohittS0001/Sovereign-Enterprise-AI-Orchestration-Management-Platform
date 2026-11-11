"""Org knowledge graph: who knows what, impact of someone's absence, bus factor."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from backend.app.api.deps import SessionDep, require_permissions
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.graph.service import (
    Absence,
    Expert,
    GraphSummary,
    build_graph,
    impact_of_absence,
    summary,
    who_knows,
)

router = APIRouter(prefix="/api/v1/graph", tags=["Knowledge graph"])
PeopleReader = Annotated[Principal, Depends(require_permissions(Permission.PEOPLE_READ))]


@router.get("/summary", response_model=GraphSummary)
async def graph_summary(
    principal: PeopleReader, session: SessionDep, request: Request
) -> GraphSummary:
    return summary(await build_graph(session, request.app.state.analytics_collector, principal))


@router.get("/who-knows", response_model=list[Expert])
async def experts(
    principal: PeopleReader,
    session: SessionDep,
    request: Request,
    topic: Annotated[str, Query(min_length=2, max_length=200)],
) -> list[Expert]:
    """People ranked by delivered work and documents about the topic, with evidence."""
    return who_knows(
        await build_graph(session, request.app.state.analytics_collector, principal), topic
    )


@router.get("/people/{person}/impact", response_model=Absence)
async def absence(
    principal: PeopleReader,
    session: SessionDep,
    request: Request,
    person: Annotated[str, Path(min_length=1, max_length=200)],
) -> Absence:
    """What stalls if this person is away: sole work, blocked work, concentrated repos, backups."""
    return impact_of_absence(
        await build_graph(session, request.app.state.analytics_collector, principal), person
    )
