"""Human approval of write actions proposed by the agent."""

import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from backend.app.core.logging import get_logger
from backend.app.core.observability import APPROVALS
from backend.app.core.security.permissions import Permission, Principal
from backend.app.db.base import as_utc, utcnow
from backend.app.db.models import AgentRun, ApprovalRequest, ApprovalStatus, RunStatus
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.governance.guardrails.redaction import redact_data
from backend.app.tools.base import ToolStatus
from backend.app.tools.registry import ToolRegistry, ToolSpec

logger = get_logger(__name__)


def describe_action(spec: ToolSpec, arguments: dict[str, Any]) -> str:
    shown = json.dumps(arguments, sort_keys=True, default=str)
    if len(shown) > 1500:
        shown = shown[:1500] + "…"
    return f"{spec.name}: {spec.description} Arguments: {shown}"


class ApprovalService:
    def __init__(self, session: AsyncSession, settings: Settings, registry: ToolRegistry):
        self.session = session
        self.settings = settings
        self.registry = registry
        self.audit = AuditService(session)

    async def create(
        self,
        agent: Principal,
        spec: ToolSpec,
        arguments: dict[str, Any],
        *,
        run_id: uuid.UUID | None,
        risk_flags: list[str],
    ) -> ApprovalRequest:
        """Queue an action for approval (caller commits)."""
        approval = ApprovalRequest(
            id=uuid.uuid4(),
            run_id=run_id,
            requested_by_id=agent.user_id,
            tool_name=spec.name,
            permission=(spec.permission or Permission.PROJECTS_WRITE).value,
            arguments=arguments,
            summary=describe_action(spec, arguments),
            risk_flags=risk_flags,
            status=ApprovalStatus.PENDING,
            expires_at=utcnow() + timedelta(hours=self.settings.approval_ttl_hours),
        )
        self.session.add(approval)
        await self.session.flush()
        await self.audit.record(
            actions.APPROVAL_REQUEST,
            status=actions.PENDING,
            principal=agent,
            resource_type="approval",
            resource_id=str(approval.id),
            run_id=run_id,
            details={"tool": spec.name, "arguments": arguments, "risk_flags": risk_flags},
        )
        return approval

    def _can_view(self, approval: ApprovalRequest, viewer: Principal) -> bool:
        return viewer.has(Permission.APPROVALS_DECIDE) or approval.requested_by_id == viewer.user_id

    async def get(self, approval_id: uuid.UUID, viewer: Principal) -> ApprovalRequest:
        await self.expire_stale()
        approval = await self.session.get(ApprovalRequest, approval_id)
        if approval is None or not self._can_view(approval, viewer):
            # Do not reveal whether someone else's approval exists.
            raise NotFoundError("Approval request not found")
        return approval

    async def list(
        self,
        viewer: Principal,
        *,
        status: ApprovalStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ApprovalRequest], int]:
        await self.expire_stale()
        filters = []
        if status is not None:
            filters.append(ApprovalRequest.status == status)
        if not viewer.has(Permission.APPROVALS_DECIDE):
            filters.append(ApprovalRequest.requested_by_id == viewer.user_id)

        total = await self.session.scalar(
            select(func.count()).select_from(ApprovalRequest).where(*filters)
        )
        items = await self.session.scalars(
            select(ApprovalRequest)
            .where(*filters)
            .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id)
            .limit(limit)
            .offset(offset)
        )
        return list(items), total or 0

    async def expire_stale(self) -> None:
        result = await self.session.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at <= utcnow(),
            )
            .values(status=ApprovalStatus.EXPIRED)
            .returning(ApprovalRequest.run_id)
        )
        rows = result.all()
        if not rows:
            return
        for run_id in {run_id for (run_id,) in rows if run_id is not None}:
            await self._settle_run(run_id)
        await self.session.commit()

    async def decide(
        self,
        approval_id: uuid.UUID,
        approver: Principal,
        *,
        approve: bool,
        note: str | None,
    ) -> ApprovalRequest:
        approval = await self.get(approval_id, approver)

        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(f"Approval request is already {approval.status.value}")
        if as_utc(approval.expires_at) <= utcnow():
            raise ConflictError("Approval request has expired")
        if not approver.has(Permission.APPROVALS_DECIDE):
            raise AuthorizationError("You cannot decide approval requests")
        # Nobody may approve an action they would not be allowed to perform themselves.
        if not approver.has(Permission(approval.permission)):
            raise AuthorizationError(
                "You lack the permission this action needs",
                details={"missing_permissions": [approval.permission]},
            )
        if (
            not self.settings.approval_allow_self_approval
            and approval.requested_by_id == approver.user_id
        ):
            raise AuthorizationError("Another person must approve your own request")

        approval.decided_by_id = approver.user_id
        approval.decided_at = utcnow()
        approval.decision_note = note

        await self.audit.record(
            actions.APPROVAL_DECIDE,
            status=actions.SUCCESS,
            principal=approver,
            resource_type="approval",
            resource_id=str(approval.id),
            run_id=approval.run_id,
            details={"decision": "approved" if approve else "rejected", "note": note},
        )

        if not approve:
            approval.status = ApprovalStatus.REJECTED
        else:
            await self._execute(approval, approver)

        if approval.run_id is not None:
            await self._settle_run(approval.run_id)
        await self.session.commit()
        APPROVALS.labels(approval.status.value).inc()
        logger.info(
            "Approval decided",
            extra={"approval_id": str(approval.id), "status": approval.status.value},
        )
        return approval

    async def _execute(self, approval: ApprovalRequest, approver: Principal) -> None:
        result = await self.registry.invoke(approval.tool_name, approval.arguments)
        succeeded = result.status == ToolStatus.SUCCESS

        approval.status = ApprovalStatus.EXECUTED if succeeded else ApprovalStatus.FAILED
        data = result.data if isinstance(result.data, dict) else {"value": result.data}
        approval.result = redact_data(json.loads(json.dumps(data, default=str)))
        approval.error = result.error

        await self.audit.record(
            actions.APPROVAL_EXECUTE,
            status=actions.SUCCESS if succeeded else actions.FAILURE,
            actor_type="agent",
            actor_user_id=approval.requested_by_id,
            resource_type="tool",
            resource_id=approval.tool_name,
            run_id=approval.run_id,
            details={
                "approval_id": str(approval.id),
                "approved_by": str(approver.user_id),
                "arguments": approval.arguments,
                "error": result.error,
            },
        )

    async def _settle_run(self, run_id: uuid.UUID) -> None:
        """Mark a run completed once none of its approvals is still pending."""
        pending = await self.session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(
                ApprovalRequest.run_id == run_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        )
        if not pending:
            run = await self.session.get(AgentRun, run_id)
            if run is not None and run.status == RunStatus.AWAITING_APPROVAL:
                run.status = RunStatus.COMPLETED
