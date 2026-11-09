"""Every tool call the agent makes passes through here.

Order of checks: tool exists -> caller has the permission -> arguments are valid ->
write actions are queued for human approval -> the tool runs. Each outcome, including
denials, is written to the audit log.
"""

import time
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.context import principal_var
from backend.app.core.logging import get_logger
from backend.app.core.observability import TOOL_CALLS, TOOL_SECONDS, span
from backend.app.core.security.permissions import Principal
from backend.app.domains.governance.approvals.service import ApprovalService
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.tools.base import ToolResult, ToolStatus
from backend.app.tools.registry import ToolRegistry, ToolSpec

logger = get_logger(__name__)

_AUDIT_STATUS = {
    ToolStatus.SUCCESS: actions.SUCCESS,
    ToolStatus.ERROR: actions.FAILURE,
    ToolStatus.DENIED: actions.DENIED,
    ToolStatus.PENDING_APPROVAL: actions.PENDING,
}


class ToolGateway:
    def __init__(self, registry: ToolRegistry, session: AsyncSession, settings: Settings):
        self.registry = registry
        self.session = session
        self.settings = settings
        self.audit = AuditService(session)
        self.approvals = ApprovalService(session, settings, registry)

    def available_tools(self, principal: Principal) -> list[ToolSpec]:
        """Tools this principal may use (the only ones shown to the planner)."""
        return [
            spec
            for spec in self.registry.specs()
            if spec.permission is None or principal.has(spec.permission)
        ]

    def needs_approval(self, principal: Principal, spec: ToolSpec, risk_flags: list[str]) -> bool:
        if not spec.is_write or principal.actor_type != "agent":
            return False
        if risk_flags:
            # Exemptions never apply once a run has seen suspicious content.
            return True
        return spec.name not in self.settings.approval_exempt_tools

    async def invoke(
        self,
        principal: Principal,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: uuid.UUID | None = None,
        risk_flags: list[str] | None = None,
    ) -> ToolResult:
        with span(
            "tool.invoke", tool=tool_name, actor=principal.actor_type, run_id=run_id
        ) as current:
            result = await self._invoke(principal, tool_name, arguments, run_id, risk_flags or [])
            current.set_attribute("tool.status", result.status.value)
            return result

    async def _invoke(
        self,
        principal: Principal,
        tool_name: str,
        arguments: dict[str, Any],
        run_id: uuid.UUID | None,
        risk_flags: list[str],
    ) -> ToolResult:
        started = time.perf_counter()
        spec = self.registry.get(tool_name)

        if spec is None:
            result = ToolResult(status=ToolStatus.ERROR, error=f"Unknown tool: {tool_name}")
        elif spec.permission is not None and not principal.has(spec.permission):
            result = ToolResult(
                status=ToolStatus.DENIED,
                error=f"Not permitted to use {spec.name} (needs {spec.permission.value})",
            )
        else:
            try:
                arguments = spec.validate(arguments)
            except ValidationError as exc:
                result = ToolResult(
                    status=ToolStatus.ERROR,
                    error=f"Invalid arguments for {spec.name}: {exc.errors(include_url=False)}",
                )
            else:
                if self.needs_approval(principal, spec, risk_flags):
                    approval = await self.approvals.create(
                        principal, spec, arguments, run_id=run_id, risk_flags=risk_flags
                    )
                    result = ToolResult(
                        status=ToolStatus.PENDING_APPROVAL,
                        approval_id=str(approval.id),
                        data={"message": "Queued for human approval", "summary": approval.summary},
                    )
                else:
                    token = principal_var.set(principal)
                    try:
                        result = await self.registry.invoke(spec.name, arguments)
                    finally:
                        principal_var.reset(token)

        await self.audit.record(
            actions.TOOL_INVOKE,
            status=_AUDIT_STATUS[result.status],
            principal=principal,
            resource_type="tool",
            resource_id=tool_name,
            run_id=run_id,
            details={
                "arguments": arguments,
                "operation": spec.operation.value if spec else None,
                "error": result.error,
                "approval_id": result.approval_id,
            },
        )
        # Commit right away: keeps the audit-chain lock short and makes the record
        # durable even if a later step of the run fails.
        await self.session.commit()

        TOOL_CALLS.labels(tool_name if spec else "unknown", result.status.value).inc()
        TOOL_SECONDS.labels(tool_name if spec else "unknown").observe(time.perf_counter() - started)
        logger.info(
            "Tool call",
            extra={"tool": tool_name, "status": result.status.value, "run_id": str(run_id)},
        )
        return result
