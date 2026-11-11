"""Correctness of project analytics on the demo dataset (deterministic golden values)."""

from datetime import date

from sqlalchemy import select

from backend.app.db.models import Project
from backend.app.domains.analytics.contradictions import find_contradictions
from backend.app.domains.analytics.forecast import forecast_sprint
from backend.app.domains.analytics.risk import assess_risk
from backend.app.domains.analytics.workload import compute_workload
from backend.app.domains.cloud.service import project_cloud
from backend.app.domains.delivery.delay import analyze_delay, collect_delay_facts
from backend.app.domains.delivery.dora import dora_metrics, incident_history
from backend.app.domains.delivery.roadmap import build_roadmap
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment


async def run() -> SuiteResult:
    data = load_dataset("analytics")
    exp = data["expected"]
    result = SuiteResult("analytics")

    as_of = data["as_of"] if isinstance(data["as_of"], date) else date.fromisoformat(data["as_of"])
    with Timer() as timer:
        # Demo data is anchored to as_of too, so golden values never drift with the calendar.
        async with environment(demo_today=as_of) as env:
            async with env.database.session() as session:
                project = await session.scalar(
                    select(Project).where(Project.key == data["project"])
                )
            snap = await env.collector.collect(project, today=as_of)
            incidents = await incident_history(env.collector.hub, snap)
            delay_facts = await collect_delay_facts(env.collector.hub, snap)
            cloud = await project_cloud(env.collector.hub, project, as_of, include_security=True)
            hidden = await project_cloud(env.collector.hub, project, as_of, include_security=False)

        forecast = forecast_sprint(snap)
        workload = compute_workload(snap)
        contradictions = find_contradictions(snap)
        risk = assess_risk(snap, forecast, workload, contradictions)
        members = {m.name: m for m in workload.members}
        pipeline = next(f for f in risk.factors if f.name == "Delivery pipeline")
        road = build_roadmap(snap)
        dora = dora_metrics(snap, incidents)
        delay = analyze_delay(snap, delay_facts, road)
        contradicted = sorted(c.said_by or "" for c in delay.claims if c.verdict == "contradicted")

        checks = {
            "forecast_status": (forecast.status, exp["forecast_status"]),
            "committed_points": (forecast.committed_points, exp["committed_points"]),
            "done_points": (forecast.done_points, exp["done_points"]),
            "working_days_left": (forecast.working_days_left, exp["working_days_left"]),
            "overloaded": (sorted(n for n, m in members.items() if m.status == "overloaded"), sorted(exp["overloaded"])),
            "at_risk": ({n: m.at_risk_issues for n, m in members.items() if m.at_risk_issues}, exp["at_risk"]),
            "suggestions": ([[s.issue, s.to_member] for s in workload.suggestions], exp["suggestions"]),
            "contradictions": (sorted([c.kind, c.issue] for c in contradictions), sorted(exp["contradictions"])),
            "blocked": (sorted(i.key for i in snap.sprint_issues if i.is_open and i.is_blocked), sorted(exp["blocked"])),
            "risk_level": (risk.level, exp["risk_level"]),
            "risk_score_in_range": (exp["risk_score_between"][0] <= risk.score <= exp["risk_score_between"][1], True),
            "critical_path": (road.critical_path, exp["critical_path"]),
            "roadmap_finish": (road.finish.isoformat(), str(exp["roadmap_finish"])),
            "dora_lead_time_days": (dora.lead_time.value, exp["dora"]["lead_time_days"]),
            "dora_change_failure_rate": (dora.change_failure_rate.value, exp["dora"]["change_failure_rate"]),
            "dora_time_to_restore_days": (dora.time_to_restore.value, exp["dora"]["time_to_restore_days"]),
            "delay_main_cause": (delay.losses[0].cause if delay.losses else None, exp["delay"]["main_cause"]),
            "delay_main_cause_issues": (delay.losses[0].issues if delay.losses else [], exp["delay"]["main_cause_issues"]),
            "delay_slip_days": (delay.slip_days, exp["delay"]["slip_days"]),
            "delay_contradicted_claims_by": (contradicted, exp["delay"]["contradicted_claims_by"]),
            "cloud_resources": (len(cloud.resources), exp["cloud"]["resources"]),
            "cloud_unhealthy": (sorted(r.name for r in cloud.resources if r.health in {"warning", "impaired"}), exp["cloud"]["unhealthy"]),
            "cloud_storage_gb": (cloud.storage_gb, exp["cloud"]["storage_gb"]),
            "cloud_cost_month_to_date": (cloud.cost.month_to_date if cloud.cost else None, exp["cloud"]["cost_month_to_date"]),
            "cloud_critical_findings": (cloud.findings_by_severity.get("critical", 0), exp["cloud"]["critical_findings"]),
            "cloud_findings_hidden_without_permission": (hidden.findings is None, True),
            "failing_main_builds": (
                any(f"{exp['failing_main_builds']} consecutive failing builds" in e for e in pipeline.evidence), True
            ),
        }  # fmt: skip
        for name, (actual, expected) in checks.items():
            result.cases.append(
                CaseResult(name, actual == expected, {"actual": actual, "expected": expected})
            )

    passed = sum(c.passed for c in result.cases)
    result.metrics = [
        Metric("accuracy", ratio(passed, len(result.cases)), data["thresholds"]["accuracy"])
    ]
    result.seconds = timer.seconds
    return result
