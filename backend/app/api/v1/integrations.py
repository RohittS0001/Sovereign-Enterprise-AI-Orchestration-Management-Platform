import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backend.app.api.deps import SettingsDep, ToolRegistryDep, require_permissions
from backend.app.core.security.permissions import Permission
from backend.app.integrations.connectors.registry import ALL_SPECS
from backend.app.integrations.hub import IntegrationHub

router = APIRouter(
    prefix="/api/v1/integrations",
    tags=["Integrations"],
    dependencies=[Depends(require_permissions(Permission.USERS_MANAGE))],
)


class IntegrationStatus(BaseModel):
    name: str
    title: str
    category: str
    mode: Literal["live", "demo", "disabled"]
    tools: list[str]
    # Env vars still needed to enable it (empty once configured) — what to fill in .env.
    missing_env: list[str]
    setup: str
    docs_url: str
    healthy: bool | None = None
    detail: str | None = None


def _tool_prefixes(name: str, category: str) -> tuple[str, ...]:
    # Core integrations own `<name>.*` tools; connectors share their category's tools.
    return (f"{name}.",) if category == "core" else (f"{category}.",)


@router.get("", response_model=list[IntegrationStatus])
async def integration_status(
    request: Request, registry: ToolRegistryDep, settings: SettingsDep, check: bool = False
) -> list[IntegrationStatus]:
    """Every known integration: active or not, its tools, and which env vars are missing.

    ``check=true`` also tests connectivity of the active ones.
    """
    hub: IntegrationHub = request.app.state.integrations
    enabled = hub.enabled()
    health = {}
    if check:
        active = [s.name for s in ALL_SPECS if enabled.get(s.name)]
        health = {r.name: r for r in await asyncio.gather(*(hub.check(n) for n in active))}

    tools = registry.list_tools()
    statuses = []
    for spec in ALL_SPECS:
        on = enabled.get(spec.name, False)
        result = health.get(spec.name)
        statuses.append(
            IntegrationStatus(
                name=spec.name,
                title=spec.title,
                category=spec.category,
                mode=hub.mode if on else "disabled",
                tools=[t for t in tools if t.startswith(_tool_prefixes(spec.name, spec.category))]
                if on
                else [],
                missing_env=[] if hub.mode == "demo" else spec.missing(settings),
                setup=spec.setup,
                docs_url=spec.docs_url,
                healthy=result.healthy if result else None,
                detail=result.detail if result else None,
            )
        )
    return statuses
