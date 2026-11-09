import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backend.app.api.deps import ToolRegistryDep, require_permissions
from backend.app.core.security.permissions import Permission
from backend.app.integrations.hub import INTEGRATIONS, IntegrationHub

router = APIRouter(
    prefix="/api/v1/integrations",
    tags=["Integrations"],
    dependencies=[Depends(require_permissions(Permission.USERS_MANAGE))],
)


class IntegrationStatus(BaseModel):
    name: str
    mode: Literal["live", "demo", "disabled"]
    tools: list[str]
    healthy: bool | None = None
    detail: str | None = None


@router.get("", response_model=list[IntegrationStatus])
async def integration_status(
    request: Request, registry: ToolRegistryDep, check: bool = False
) -> list[IntegrationStatus]:
    """Which integrations are active and their tools; ``check=true`` also tests connectivity."""
    hub: IntegrationHub = request.app.state.integrations
    enabled = hub.enabled()
    health = {}
    if check:
        results = await asyncio.gather(*(hub.check(name) for name in INTEGRATIONS if enabled[name]))
        health = {r.name: r for r in results}

    statuses = []
    for name in INTEGRATIONS:
        result = health.get(name)
        statuses.append(
            IntegrationStatus(
                name=name,
                mode=hub.mode if enabled[name] else "disabled",
                tools=[t for t in registry.list_tools() if t.startswith(f"{name}.")],
                healthy=result.healthy if result else None,
                detail=result.detail if result else None,
            )
        )
    return statuses
