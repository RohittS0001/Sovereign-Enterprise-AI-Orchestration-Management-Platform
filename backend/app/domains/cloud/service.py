"""What a project runs in the cloud, where and why, how healthy it is, what it stores and costs.

``project_cloud`` asks every connected cloud (AWS, Azure) for the resources tagged with the
project, their cost this month and forecast, firing alarms and active security findings, and
summarizes them: counts by kind and state, storage by kind, where it is deployed (regions and
environments), who owns what, cost by service. ``portfolio`` does the same for every active
project side by side, with the team on each project. A cloud that fails is reported as a
warning; the others still answer. Security findings are only included for principals with
``audit:read`` (admins, auditors).
"""

import asyncio
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from backend.app.db.models import Project
from backend.app.integrations.connectors.base import (
    CloudAlert,
    CloudResource,
    CostLine,
    CostReport,
    SecurityFinding,
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}
_AWS_ZONE = re.compile(r"^([a-z]{2}(?:-gov)?-[a-z]+-\d)[a-z]$")


def region_of(location: str | None) -> str:
    """The region of a location: AWS availability zones ("ap-south-1a") fold into "ap-south-1"."""
    if not location:
        return "unknown"
    match = _AWS_ZONE.match(location)
    return match.group(1) if match else location


class Placement(BaseModel):
    region: str
    environment: str | None
    resources: int


class CostSummary(BaseModel):
    currency: str
    month_to_date: float
    forecast_month_end: float | None
    by_service: list[CostLine]
    by_cloud: dict[str, float]


class ProjectCloud(BaseModel):
    project: str
    name: str
    as_of: date
    clouds: list[str]
    resources: list[CloudResource]
    counts: dict[str, int] = Field(description="Resources by kind")
    states: dict[str, int] = Field(description="Resources by health: ok, warning, impaired …")
    storage_gb: float
    storage_by_kind: dict[str, float]
    placements: list[Placement]
    owners: dict[str, int]
    cost: CostSummary | None
    alerts: list[CloudAlert]
    findings: list[SecurityFinding] | None = Field(description="None when not allowed to see them")
    findings_by_severity: dict[str, int]
    warnings: list[str]


class PortfolioRow(BaseModel):
    project: str
    name: str
    team: list[str]
    resources: int
    active: int
    unhealthy: int
    storage_gb: float
    cost_month_to_date: float | None
    cost_forecast: float | None
    currency: str | None
    alerts: int
    critical_findings: int | None
    open_tickets: int | None = None
    regions: list[str]
    warnings: list[str]


class Portfolio(BaseModel):
    as_of: date
    projects: list[PortfolioRow]
    totals: dict[str, float]
    people: dict[str, list[str]] = Field(description="Person → projects they work on")


async def _open_tickets(hub: Any, project: Project) -> int | None:
    """Open customer tickets tagged with the project (None when no support tool is connected)."""
    services = hub.by_category("support")
    if not services:
        return None
    tags = list((project.links or {}).get("zendesk_tags") or [project.key.lower()])
    total = 0
    for service in services.values():
        try:
            total += len(await service.list_tickets(tags, open_only=True, limit=100))
        except Exception:  # noqa: BLE001, S112 - a support outage must not break the portfolio
            continue
    return total


def tag_values(project: Project) -> list[str]:
    return list((project.links or {}).get("cloud_tag_values") or [project.key])


async def _from_cloud(name: str, service: Any, values: list[str], today: date) -> dict[str, Any]:
    resources = await service.resources(values)
    ids = {r.id for r in resources}
    costs, alerts, findings = await asyncio.gather(
        service.costs(values, today),
        service.alerts(values, ids),
        service.findings(values, ids),
    )
    return {"resources": resources, "cost": costs, "alerts": alerts, "findings": findings}


def _merge_costs(reports: list[CostReport]) -> CostSummary | None:
    if not reports:
        return None
    by_service: dict[str, float] = defaultdict(float)
    for report in reports:
        for line in report.by_service:
            by_service[f"{line.service}"] += line.amount
    forecasts = [r.forecast_month_end for r in reports if r.forecast_month_end is not None]
    return CostSummary(
        currency=reports[0].currency,
        month_to_date=round(sum(r.total for r in reports), 2),
        forecast_month_end=round(sum(forecasts), 2) if forecasts else None,
        by_service=sorted(
            (CostLine(service=s, amount=round(a, 2)) for s, a in by_service.items()),
            key=lambda line: -line.amount,
        ),
        by_cloud={r.source: r.total for r in reports},
    )


async def project_cloud(
    hub: Any, project: Project, today: date, *, include_security: bool
) -> ProjectCloud:
    values = tag_values(project)
    clouds = hub.by_category("cloud")
    results = await asyncio.gather(
        *(_from_cloud(name, service, values, today) for name, service in clouds.items()),
        return_exceptions=True,
    )
    resources: list[CloudResource] = []
    reports: list[CostReport] = []
    alerts: list[CloudAlert] = []
    findings: list[SecurityFinding] = []
    warnings: list[str] = []
    for name, result in zip(clouds, results, strict=True):
        if isinstance(result, BaseException):
            warnings.append(f"{name}: {getattr(result, 'message', None) or result}")
            continue
        resources += result["resources"]
        alerts += result["alerts"]
        findings += result["findings"]
        if result["cost"] is not None:
            reports.append(result["cost"])

    storage: dict[str, float] = defaultdict(float)
    for r in resources:
        if r.storage_gb:
            storage[r.kind] += r.storage_gb
    placements = Counter((region_of(r.region), r.environment) for r in resources)
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 5), f.title))
    return ProjectCloud(
        project=project.key,
        name=project.name,
        as_of=today,
        clouds=sorted(clouds),
        resources=sorted(resources, key=lambda r: (r.kind, r.name)),
        counts=dict(Counter(r.kind for r in resources)),
        states=dict(Counter(r.health for r in resources)),
        storage_gb=round(sum(storage.values()), 2),
        storage_by_kind={k: round(v, 2) for k, v in sorted(storage.items())},
        placements=[
            Placement(region=region, environment=env, resources=n)
            for (region, env), n in placements.most_common()
        ],
        owners=dict(Counter(r.owner for r in resources if r.owner)),
        cost=_merge_costs(reports),
        alerts=sorted(alerts, key=lambda a: a.started or today, reverse=True),
        findings=findings if include_security else None,
        findings_by_severity=dict(Counter(f.severity for f in findings)),
        warnings=warnings,
    )


async def portfolio(
    hub: Any, projects: list[Project], today: date, *, include_security: bool
) -> Portfolio:
    rows = []
    people: dict[str, set[str]] = defaultdict(set)
    summaries = await asyncio.gather(
        *(project_cloud(hub, p, today, include_security=include_security) for p in projects)
    )
    tickets = await asyncio.gather(*(_open_tickets(hub, p) for p in projects))
    for project, cloud, open_tickets in zip(projects, summaries, tickets, strict=True):
        team = [m.get("name", "") for m in project.members or [] if m.get("name")]
        for person in team:
            people[person].add(project.key)
        for owner in cloud.owners:
            people[owner].add(project.key)
        rows.append(
            PortfolioRow(
                project=project.key,
                name=project.name,
                team=team,
                resources=len(cloud.resources),
                active=sum(r.health in {"ok", "warning", "impaired"} for r in cloud.resources),
                unhealthy=sum(r.health in {"warning", "impaired"} for r in cloud.resources),
                storage_gb=cloud.storage_gb,
                cost_month_to_date=cloud.cost.month_to_date if cloud.cost else None,
                cost_forecast=cloud.cost.forecast_month_end if cloud.cost else None,
                currency=cloud.cost.currency if cloud.cost else None,
                alerts=len(cloud.alerts),
                critical_findings=cloud.findings_by_severity.get("critical", 0)
                if include_security
                else None,
                regions=sorted({p.region for p in cloud.placements}),
                warnings=cloud.warnings,
                open_tickets=open_tickets,
            )
        )
    totals = {
        "projects": len(rows),
        "resources": sum(r.resources for r in rows),
        "unhealthy": sum(r.unhealthy for r in rows),
        "storage_gb": round(sum(r.storage_gb for r in rows), 2),
        "cost_month_to_date": round(sum(r.cost_month_to_date or 0 for r in rows), 2),
        "cost_forecast": round(sum(r.cost_forecast or 0 for r in rows), 2),
        "alerts": sum(r.alerts for r in rows),
        "open_tickets": sum(r.open_tickets or 0 for r in rows),
    }
    return Portfolio(
        as_of=today,
        projects=rows,
        totals=totals,
        people={p: sorted(keys) for p, keys in sorted(people.items())},
    )
