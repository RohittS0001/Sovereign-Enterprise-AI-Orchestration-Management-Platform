import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select

from backend.app.analytics.service import get_project
from backend.app.api.deps import SessionDep, SettingsDep, ToolRegistryDep, require_permissions
from backend.app.audit import actions
from backend.app.audit.service import AuditService
from backend.app.core.exceptions.exceptions import AppException, NotFoundError
from backend.app.core.security.permissions import Permission, Principal
from backend.app.db.models import Report
from backend.app.mcp.schemas.common import ToolResult, ToolStatus
from backend.app.orchestration.tool_gateway import ToolGateway
from backend.app.schemas.projects import (
    ReportEmail,
    ReportGenerate,
    ReportPage,
    ReportRead,
    ReportSummary,
)
from backend.app.workflows.reporting.generator import generate_report

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])

Reader = Annotated[Principal, Depends(require_permissions(Permission.REPORTS_READ))]
Generator = Annotated[Principal, Depends(require_permissions(Permission.REPORTS_GENERATE))]
Mailer = Annotated[Principal, Depends(require_permissions(Permission.EMAIL_SEND))]


@router.post("/generate", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def generate(
    body: ReportGenerate, request: Request, principal: Generator, session: SessionDep
) -> ReportRead:
    models = request.app.state.agent_models
    report = await generate_report(
        session,
        request.app.state.analytics_collector,
        body.project_key,
        model=models.reporting if models else None,
        trigger="manual",
        principal=principal,
    )
    return ReportRead.model_validate(report)


@router.get("", response_model=ReportPage)
async def list_reports(
    principal: Reader,
    session: SessionDep,
    project_key: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReportPage:
    conditions = [Report.project_key == project_key.upper()] if project_key else []
    total = await session.scalar(select(func.count()).select_from(Report).where(*conditions))
    reports = await session.scalars(
        select(Report)
        .where(*conditions)
        .order_by(Report.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return ReportPage(
        items=[ReportSummary.model_validate(r) for r in reports],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{report_id}", response_model=ReportRead)
async def read_report(report_id: uuid.UUID, principal: Reader, session: SessionDep) -> ReportRead:
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFoundError("Report not found")
    return ReportRead.model_validate(report)


@router.post("/{report_id}/email-draft")
async def email_draft(
    report_id: uuid.UUID,
    body: ReportEmail,
    principal: Mailer,
    session: SessionDep,
    settings: SettingsDep,
    registry: ToolRegistryDep,
) -> ToolResult:
    """Create a Gmail draft of the report for a person to review and send."""
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFoundError("Report not found")
    project = await get_project(session, report.project_key)
    recipients = [str(r) for r in body.to] or project.report_recipients
    if not recipients:
        raise AppException(
            "No recipients given or configured for this project", 422, code="validation_error"
        )
    if registry.get("gmail.create_draft") is None:
        raise AppException("Gmail is not connected", 503, code="service_unavailable")

    result = await ToolGateway(registry, session, settings).invoke(
        principal,
        "gmail.create_draft",
        {
            "to": recipients,
            "subject": f"Weekly status — {project.name} ({report.period_start:%d %b %Y})",
            "body": report.content_markdown,
        },
    )
    await AuditService(session).record(
        actions.REPORT_EMAIL_DRAFT,
        status=actions.SUCCESS if result.status == ToolStatus.SUCCESS else actions.FAILURE,
        principal=principal,
        resource_type="report",
        resource_id=str(report.id),
        details={"recipients": recipients, "error": result.error},
    )
    await session.commit()
    return result
