import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.app.api.deps import (
    AgentModelsDep,
    DatabaseDep,
    PrincipalDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    ToolRegistryDep,
    require_permissions,
)
from backend.app.core.errors import AppException
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.agent.schemas import (
    AgentQueryRequest,
    AgentRunPage,
    AgentRunResponse,
    AgentToolInfo,
)
from backend.app.domains.agent.service import AgentService, get_run, list_runs, run_to_response
from backend.app.domains.usage.service import enforce_daily_quota
from backend.app.tools.gateway import ToolGateway

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
    await enforce_daily_quota(session, principal.user_id, settings.agent_daily_token_quota)
    run = await AgentService(session, settings, models, registry).run(principal, query)
    return run_to_response(run)


@router.post("/query/stream")
async def agent_query_stream(
    request: AgentQueryRequest,
    principal: AgentUser,
    session: SessionDep,
    database: DatabaseDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    models: AgentModelsDep,
    registry: ToolRegistryDep,
) -> StreamingResponse:
    """Same as /query, streamed as Server-Sent Events.

    Events: ``run_started``, ``step`` (one per tool call), ``phase``, then ``done`` with
    the full run (or ``error``). Use fetch() with a ReadableStream (EventSource cannot
    send the Authorization header).
    """
    query = request.normalized_query()
    if not query:
        raise AppException("Query must not be blank", 422, code="validation_error")
    await limiter.hit(
        f"agent:user:{principal.user_id}",
        limit=settings.agent_rate_limit_per_minute,
        window_seconds=60,
    )
    await enforce_daily_quota(session, principal.user_id, settings.agent_daily_token_quota)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def produce() -> None:
        # Own session: request-scoped dependencies may close before streaming ends.
        try:
            async with database.session() as run_session:
                run = await AgentService(run_session, settings, models, registry).run(
                    principal, query, on_event=queue.put
                )
                await queue.put(
                    {"type": "done", "run": run_to_response(run).model_dump(mode="json")}
                )
        except Exception:
            await queue.put({"type": "error", "message": "The agent hit an internal error"})
        finally:
            await queue.put(None)

    # The run continues (and is saved and audited) even if the client disconnects.
    task = asyncio.create_task(produce())

    async def events() -> AsyncIterator[str]:
        while (event := await queue.get()) is not None:
            yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
        await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
