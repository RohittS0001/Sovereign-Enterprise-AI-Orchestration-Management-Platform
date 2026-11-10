"""Project analytics facade: configuration lookup + snapshot + all calculations."""

from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import NotFoundError
from backend.app.db.models import Project
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.contradictions import find_contradictions
from backend.app.domains.analytics.evm import earned_value
from backend.app.domains.analytics.forecast import forecast_sprint
from backend.app.domains.analytics.results import ProductionHealth, ProjectHealth
from backend.app.domains.analytics.risk import assess_risk
from backend.app.domains.analytics.workload import compute_workload


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
        production=ProductionHealth(
            **{name: [item.model_dump(mode="json", exclude_none=True) for item in items]
               for name, items in vars(snapshot.ops).items()}
        ) if snapshot.ops else None,
        finance=value.model_dump(mode="json") if (value := earned_value(snapshot)) else None,
        commitments=[d.model_dump(mode="json", exclude_none=True) for d in snapshot.deals]
        if snapshot.deals is not None else None,
    )  # fmt: skip
