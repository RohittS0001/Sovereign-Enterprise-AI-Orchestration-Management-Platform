"""Compliance evidence pack (SOC 2 / ISO 27001) for auditors and admins."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from backend.app.api.deps import SessionDep, SettingsDep, require_permissions
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.compliance.service import ComplianceReport, build_report

router = APIRouter(prefix="/api/v1/compliance", tags=["Compliance"])
Auditor = Annotated[Principal, Depends(require_permissions(Permission.AUDIT_READ))]


@router.get("/report", response_model=ComplianceReport)
async def compliance_report(
    principal: Auditor,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
    format: Annotated[Literal["json", "markdown"], Query()] = "json",
) -> ComplianceReport | PlainTextResponse:
    """Status and evidence per control; ``format=markdown`` returns a document to share."""
    report = await build_report(
        session,
        settings,
        request.app.state.analytics_collector,
        request.app.state.integrations.enabled(),
    )
    if format == "markdown":
        return PlainTextResponse(report.markdown(), media_type="text/markdown; charset=utf-8")
    return report
