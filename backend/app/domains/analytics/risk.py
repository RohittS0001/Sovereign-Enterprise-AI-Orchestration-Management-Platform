"""Explainable project risk score (0-100) built from weighted, evidence-backed factors."""

from backend.app.domains.analytics.results import (
    Contradiction,
    RiskAssessment,
    RiskFactor,
    SprintForecast,
    Workload,
)
from backend.app.domains.analytics.snapshot import ProjectSnapshot

REVIEW_STALE_DAYS = 3


def _cap(value: float, maximum: float) -> float:
    return round(min(maximum, max(0.0, value)), 1)


def assess_risk(
    snapshot: ProjectSnapshot,
    forecast: SprintForecast | None,
    workload: Workload,
    contradictions: list[Contradiction],
) -> RiskAssessment:
    today = snapshot.today
    scope = snapshot.sprint_issues or snapshot.issues
    open_issues = [i for i in scope if i.is_open]
    factors: list[RiskFactor] = []

    overdue = [i for i in open_issues if i.due and i.due < today]
    factors.append(
        RiskFactor(
            name="Overdue work",
            score=_cap(sum(4 * i.priority_weight for i in overdue), 20),
            max_score=20,
            evidence=[f"{i.key} ({i.priority}) due {i.due}, {i.status}" for i in overdue],
            recommendation="Re-plan or escalate overdue high-priority items" if overdue else None,
        )
    )

    blocked = [i for i in open_issues if i.is_blocked]
    factors.append(
        RiskFactor(
            name="Blocked work",
            score=_cap(sum(5 * i.priority_weight for i in blocked), 15),
            max_score=15,
            evidence=[
                f"{i.key} blocked" + (f" by {', '.join(i.blocked_by)}" if i.blocked_by else "")
                for i in blocked
            ],
            recommendation="Escalate blockers or activate fallback plans" if blocked else None,
        )
    )

    if forecast and forecast.status != "unknown":
        schedule = 25 * max(0.0, 1 - forecast.completion_ratio) / 0.5  # ratio ≤0.5 → full 25
        factors.append(
            RiskFactor(
                name="Sprint schedule",
                score=_cap(schedule, 25),
                max_score=25,
                evidence=[
                    f"{forecast.done_points:g}/{forecast.committed_points:g} points done, "
                    f"projected {forecast.projected_points:g} by {forecast.end} ({forecast.status})"
                ],
                recommendation="Cut or defer scope to match the burn rate"
                if forecast.status != "on_track"
                else None,
            )
        )

    overloaded = [m for m in workload.members if m.status == "overloaded"]
    at_risk = [(m.name, key) for m in workload.members for key in m.at_risk_issues]
    factors.append(
        RiskFactor(
            name="Team capacity",
            score=_cap(4 * len(overloaded) + 3 * len(at_risk) + len(workload.unassigned_open), 15),
            max_score=15,
            evidence=[f"{m.name} at {m.load_ratio:.0%} of capacity" for m in overloaded]
            + [f"{key} due while {name} is on leave" for name, key in at_risk]
            + (
                [f"Unassigned: {', '.join(workload.unassigned_open)}"]
                if workload.unassigned_open
                else []
            ),
            recommendation="Apply the suggested reassignments" if workload.suggestions else None,
        )
    )

    main_runs = sorted(
        (r for r in snapshot.runs if r.branch == snapshot.default_branch and r.conclusion),
        key=lambda r: r.created or today,
        reverse=True,
    )
    failing_streak = 0
    for run in main_runs:
        if run.conclusion in {"failure", "timed_out"}:
            failing_streak += 1
        else:
            break
    stale_reviews = [
        p for p in snapshot.pulls
        if p.state == "open" and not p.draft and p.created
        and (today - p.created).days > REVIEW_STALE_DAYS
        and "APPROVED" not in p.review_states
    ]  # fmt: skip
    changes_requested = [
        p for p in snapshot.pulls if p.state == "open" and "CHANGES_REQUESTED" in p.review_states
    ]
    factors.append(
        RiskFactor(
            name="Delivery pipeline",
            score=_cap(5 * failing_streak + 2 * len(stale_reviews) + len(changes_requested), 15),
            max_score=15,
            evidence=(
                [f"{failing_streak} consecutive failing builds on {snapshot.default_branch}"]
                if failing_streak
                else []
            )
            + [
                f"PR #{p.number} waiting for review {(today - p.created).days} days"
                for p in stale_reviews
            ]
            + [f"PR #{p.number} has changes requested" for p in changes_requested],
            recommendation="Fix the main build before merging more work"
            if failing_streak >= 2
            else None,
        )
    )

    factors.append(
        RiskFactor(
            name="Tracking accuracy",
            score=_cap(3 * len(contradictions), 10),
            max_score=10,
            evidence=[c.detail for c in contradictions],
            recommendation="Update Jira statuses to match the code" if contradictions else None,
        )
    )

    if snapshot.ops is not None:
        factors.append(production_factor(snapshot))
    if snapshot.finance:
        from backend.app.domains.analytics.evm import budget_factor, earned_value

        value = earned_value(snapshot)
        if value is not None and value.cpi is not None:
            factors.append(budget_factor(value))

    covered = sum(f.max_score for f in factors)
    raw = sum(f.score for f in factors)
    score = round(100 * raw / covered) if covered else 0
    level = "high" if score >= 60 else "medium" if score >= 30 else "low"
    return RiskAssessment(score=score, level=level, factors=factors)


HIGH_SEVERITY = {"high", "critical", "p1", "p2", "sev1", "sev2", "blocker"}
NOISY_ERROR_EVENTS = 100


def production_factor(snapshot: ProjectSnapshot) -> RiskFactor:
    """Open incidents, failing quality gates, failed deployments and noisy errors."""
    ops = snapshot.ops
    assert ops is not None  # noqa: S101 - caller checks
    high = [i for i in ops.incidents if (i.severity or "").lower() in HIGH_SEVERITY]
    other = [i for i in ops.incidents if i not in high]
    gates = [g for g in ops.quality_gates if g.status == "ERROR"]
    failed = [d for d in ops.deployments if d.status in {"failure", "out_of_sync"}]
    noisy = [e for e in ops.errors if e.events >= NOISY_ERROR_EVENTS]
    score = 5 * len(high) + 2 * len(other) + 4 * len(gates) + 3 * len(failed) + len(noisy)
    evidence = (
        [f"{i.source} incident {i.id} ({i.severity}, {i.status}): {i.title}" for i in ops.incidents]
        + [
            f"Quality gate failing on {g.project}: {'; '.join(g.failing) or g.status}"
            for g in gates
        ]
        + [f"Deployment {d.name}: {d.status} ({d.detail or d.source})" for d in failed]
        + [f"{e.id}: {e.events} events / {e.users} users — {e.title}" for e in noisy]
    )
    return RiskFactor(
        name="Production health",
        score=_cap(score, 15),
        max_score=15,
        evidence=evidence,
        recommendation=(
            "Stabilize production (incidents, failing deploys, quality gate) before adding scope"
            if score
            else None
        ),
    )
