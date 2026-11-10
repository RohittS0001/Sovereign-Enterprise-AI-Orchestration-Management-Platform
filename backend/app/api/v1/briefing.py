"""Chief-of-staff brief (and its voice script) and drafted escalations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from backend.app.api.deps import SessionDep, SettingsDep, ToolRegistryDep, require_permissions
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.briefing.service import (
    PortfolioBrief,
    portfolio_brief,
    propose_escalations,
)
from backend.app.tools.gateway import ToolGateway

router = APIRouter(prefix="/api/v1/briefing", tags=["Chief of staff"])

Analyst = Annotated[Principal, Depends(require_permissions(Permission.ANALYTICS_READ))]
Agent = Annotated[
    Principal, Depends(require_permissions(Permission.AGENT_QUERY, Permission.ANALYTICS_READ))
]


@router.get("", response_model=PortfolioBrief)
async def get_brief(principal: Analyst, session: SessionDep, request: Request) -> PortfolioBrief:
    """Every active project: risk, forecast, recommendation, decisions needed, voice script."""
    return await portfolio_brief(session, request.app.state.analytics_collector)


@router.post("/escalations")
async def draft_escalations(
    principal: Agent, session: SessionDep, settings: SettingsDep, registry: ToolRegistryDep,
    request: Request,
) -> list[dict]:  # fmt: skip
    """Draft escalation e-mails for projects needing a decision; each waits for approval."""
    brief = await portfolio_brief(session, request.app.state.analytics_collector)
    return await propose_escalations(ToolGateway(registry, session, settings), principal, brief)
