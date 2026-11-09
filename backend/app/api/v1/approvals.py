import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.deps import PrincipalDep, SessionDep, SettingsDep, ToolRegistryDep
from backend.app.db.models import ApprovalStatus
from backend.app.domains.governance.approvals.schemas import (
    ApprovalDecision,
    ApprovalPage,
    ApprovalRead,
)
from backend.app.domains.governance.approvals.service import ApprovalService

router = APIRouter(prefix="/api/v1/approvals", tags=["Approvals"])


@router.get("", response_model=ApprovalPage)
async def list_approvals(
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
    status: ApprovalStatus | None = ApprovalStatus.PENDING,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalPage:
    """Approvers see every request; everyone else sees only their own."""
    items, total = await ApprovalService(session, settings, registry).list(
        principal, status=status, limit=limit, offset=offset
    )
    return ApprovalPage(
        items=[ApprovalRead.model_validate(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{approval_id}", response_model=ApprovalRead)
async def get_approval(
    approval_id: uuid.UUID,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
) -> ApprovalRead:
    approval = await ApprovalService(session, settings, registry).get(approval_id, principal)
    return ApprovalRead.model_validate(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalRead)
async def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
) -> ApprovalRead:
    """Approve and immediately execute the action."""
    approval = await ApprovalService(session, settings, registry).decide(
        approval_id, principal, approve=True, note=body.note
    )
    return ApprovalRead.model_validate(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalRead)
async def reject(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
) -> ApprovalRead:
    approval = await ApprovalService(session, settings, registry).decide(
        approval_id, principal, approve=False, note=body.note
    )
    return ApprovalRead.model_validate(approval)
