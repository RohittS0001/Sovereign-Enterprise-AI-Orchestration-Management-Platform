import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.api.deps import (
    AgentModelsDep,
    PrincipalDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    ToolRegistryDep,
    require_permissions,
)
from backend.app.core.exceptions.exceptions import AppException
from backend.app.core.security.permissions import Permission, Principal
from backend.app.orchestration.tool_gateway import ToolGateway
from backend.app.schemas.agent import (
    AgentQueryRequest,
    AgentRunPage,
    AgentRunResponse,
    AgentToolInfo,
)
from backend.app.services.agent_service import AgentService, get_run, list_runs, run_to_response

router = APIRouter(
    prefix="/api/v1/agent",
    tags=["Agent"],
)

AgentUser = Annotated[Principal, Depends(require_permissions(Permission.AGENT_QUERY))]


@router.post("/query", response_model=AgentRunResponse)
async def agent_query(
    request: AgentQueryRequest,
    principal: AgentUser,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    models: AgentModelsDep,
    registry: ToolRegistryDep,
) -> AgentRunResponse:
    """Ask the agent a question. Write actions it proposes wait for human approval."""
    query = request.normalized_query()
    if not query:
        raise AppException("Query must not be blank", 422, code="validation_error")

    await limiter.hit(
        f"agent:user:{principal.user_id}",
        limit=settings.agent_rate_limit_per_minute,
        window_seconds=60,
    )
    run = await AgentService(session, settings, models, registry).run(principal, query)
    return run_to_response(run)


@router.get("/runs", response_model=AgentRunPage)
async def agent_runs(
    principal: AgentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentRunPage:
    runs, total = await list_runs(session, principal, limit=limit, offset=offset)
    return AgentRunPage(
        items=[run_to_response(r) for r in runs], total=total, limit=limit, offset=offset
    )


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def agent_run(
    run_id: uuid.UUID, principal: AgentUser, session: SessionDep
) -> AgentRunResponse:
    return run_to_response(await get_run(session, run_id, principal))


@router.get("/tools", response_model=list[AgentToolInfo])
async def agent_tools(
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
) -> list[AgentToolInfo]:
    """Tools the agent can use on your behalf, and which need approval."""
    agent = principal.as_agent()
    gateway = ToolGateway(registry, session, settings)
    return [
        AgentToolInfo(
            name=spec.name,
            description=spec.description,
            operation=spec.operation.value,
            requires_approval=gateway.needs_approval(agent, spec, []),
            input_schema=spec.input_schema(),
        )
        for spec in gateway.available_tools(agent)
    ]
