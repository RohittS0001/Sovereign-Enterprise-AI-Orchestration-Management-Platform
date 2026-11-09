from typing import Annotated

from fastapi import APIRouter, Depends

from backend.app.api.deps import RateLimiterDep, SettingsDep, require_permissions
from backend.app.core.security.permissions import Permission, Principal
from backend.app.schemas.agent import AgentQueryRequest, AgentQueryResponse
from backend.app.services.agent_service import process_agent_request

router = APIRouter(
    prefix="/api/v1/agent",
    tags=["Agent"],
)


@router.post("/query", response_model=AgentQueryResponse)
async def agent_query(
    request: AgentQueryRequest,
    principal: Annotated[Principal, Depends(require_permissions(Permission.AGENT_QUERY))],
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> AgentQueryResponse:
    await limiter.hit(
        f"agent:user:{principal.user_id}",
        limit=settings.agent_rate_limit_per_minute,
        window_seconds=60,
    )
    return process_agent_request(request, principal.as_agent())
