import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.api.deps import SessionDep, require_permissions
from backend.app.audit.service import AuditService
from backend.app.core.security.permissions import Permission
from backend.app.schemas.agent import AuditEventRead, AuditPage, AuditVerification

router = APIRouter(
    prefix="/api/v1/audit",
    tags=["Audit"],
    dependencies=[Depends(require_permissions(Permission.AUDIT_READ))],
)


@router.get("", response_model=AuditPage)
async def list_events(
    session: SessionDep,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    events, total = await AuditService(session).list(
        limit=limit, offset=offset, action=action, actor_user_id=actor_user_id, run_id=run_id
    )
    return AuditPage(
        items=[AuditEventRead.model_validate(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/verify", response_model=AuditVerification)
async def verify_chain(session: SessionDep) -> AuditVerification:
    """Recompute the hash chain to detect edited, deleted or reordered events."""
    result = await AuditService(session).verify_chain()
    return AuditVerification(
        valid=result.valid,
        checked=result.checked,
        first_invalid_seq=result.first_invalid_seq,
        reason=result.reason,
    )
