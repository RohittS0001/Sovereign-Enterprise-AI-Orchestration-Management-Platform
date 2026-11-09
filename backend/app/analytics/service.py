"""Project analytics facade: configuration lookup + snapshot + all calculations."""

from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.analytics.collector import SnapshotCollector
from backend.app.analytics.metrics.results import ProjectHealth
from backend.app.analytics.risks.score import assess_risk
from backend.app.analytics.trends.forecast import forecast_sprint
from backend.app.analytics.workload.workload import compute_workload
from backend.app.core.exceptions.exceptions import NotFoundError
from backend.app.db.models import Project
from backend.app.reasoning.contradiction.checks import find_contradictions


async def get_project(session: AsyncSession, key: str) -> Project:
    project = await session.scalar(select(Project).where(Project.key == key.upper()))
    if project is None:
        raise NotFoundError(f"Project {key} is not configured")
    return project


async def project_health(
    session: AsyncSession,
    collector: SnapshotCollector,
    key: str,
    *,
    today: date | None = None,
    fresh: bool = False,
) -> ProjectHealth:
    project = await get_project(session, key)
    snapshot = await collector.collect(project, today=today, fresh=fresh)

    forecast = forecast_sprint(snapshot)
    workload = compute_workload(snapshot)
    contradictions = find_contradictions(snapshot)
    risk = assess_risk(snapshot, forecast, workload, contradictions)
    scope = snapshot.sprint_issues or snapshot.issues

    return ProjectHealth(
        project=project.key,
        name=project.name,
        as_of=snapshot.today,
        risk=risk,
        forecast=forecast,
        workload=workload,
        contradictions=contradictions,
        issue_counts=dict(Counter(i.category for i in scope)),
        warnings=snapshot.warnings,
    )
