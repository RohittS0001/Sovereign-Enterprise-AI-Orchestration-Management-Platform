from datetime import date

import pytest

from backend.app.core.config import Settings
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.contradictions import find_contradictions
from backend.app.domains.analytics.forecast import forecast_sprint, working_days
from backend.app.domains.analytics.risk import assess_risk
from backend.app.domains.analytics.snapshot import ProjectSnapshot, normalize_issue, normalize_pull
from backend.app.domains.analytics.workload import compute_workload
from backend.app.domains.reporting.generator import render_template, week_bounds
from backend.app.integrations.hub import IntegrationHub, build_integration_hub
from tests.support.analytics import phx_project

MONDAY = date(2026, 11, 9)


@pytest.fixture
async def snapshot() -> ProjectSnapshot:
    settings = Settings(_env_file=None, demo_mode=True)
    collector = SnapshotCollector(build_integration_hub(settings), settings)
    return await collector.collect(phx_project(), today=MONDAY)


# ------------------------------------------------------------------ normalization


def test_live_jira_issue_shape_is_normalized() -> None:
    raw = {
        "key": "PAY-9",
        "fields": {
            "summary": "Retry queue",
            "status": {"name": "QA Verification", "statusCategory": {"key": "indeterminate"}},
            "priority": {"name": "High"},
            "assignee": {"displayName": "A", "accountId": "x1"},
            "duedate": "2026-11-20",
            "customfield_10016": 5.0,
            "issuetype": {"name": "Story"},
            "updated": "2026-11-08T10:15:30.000+0530",
            "issuelinks": [
                {
                    "type": {"name": "Blocks", "inward": "is blocked by"},
                    "inwardIssue": {"key": "PAY-2"},
                },
                {
                    "type": {"name": "Relates", "inward": "relates to"},
                    "inwardIssue": {"key": "PAY-3"},
                },
            ],
        },
    }

    issue = normalize_issue(raw, "customfield_10016")

    assert issue.category == "in_progress"  # from statusCategory, not the custom name
    assert issue.points == 5.0
    assert issue.blocked_by == ["PAY-2"] and issue.is_blocked
    assert issue.due == date(2026, 11, 20)
    assert issue.priority_weight == 2.0


def test_pull_request_issue_keys_come_from_title_and_branch() -> None:
    pull = normalize_pull("acme/x", {"number": 1, "title": "Fix retry", "head": "pay-12-retry"})

    assert pull.issue_keys == ["PAY-12"]


# ------------------------------------------------------------------ forecast


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 11, 9), date(2026, 11, 13), 5),  # Mon-Fri
        (date(2026, 11, 7), date(2026, 11, 8), 0),  # weekend
        (date(2026, 10, 31), date(2026, 11, 14), 10),
        (date(2026, 11, 13), date(2026, 11, 9), 0),
    ],
)
def test_working_days(start, end, expected) -> None:
    assert working_days(start, end) == expected


def test_demo_sprint_forecast(snapshot) -> None:
    forecast = forecast_sprint(snapshot)

    assert (forecast.committed_points, forecast.done_points) == (31, 7)
    assert (forecast.working_days_elapsed, forecast.working_days_left) == (5, 5)
    assert forecast.burn_rate_per_day == 1.4
    assert forecast.projected_points == 14.0
    assert forecast.status == "off_track"
    assert "PHX-7" in forecast.remaining_issues and "PHX-1" not in forecast.remaining_issues


def test_forecast_without_sprint_or_points(snapshot) -> None:
    snapshot.sprint.start = None
    assert forecast_sprint(snapshot).status == "unknown"

    snapshot.sprint = None
    assert forecast_sprint(snapshot) is None


# ------------------------------------------------------------------ workload


def test_leave_aware_workload_and_suggestions(snapshot) -> None:
    workload = compute_workload(snapshot)
    members = {m.name: m for m in workload.members}

    arjun = members["Arjun M"]
    assert arjun.leave_days == 4
    assert arjun.availability == 0.2
    assert arjun.status == "overloaded"
    assert arjun.at_risk_issues == ["PHX-5"]
    assert members["Meera S"].status == "balanced"
    assert members["Dev R"].status == "underused"
    # Only not-started, unblocked work is suggested for reassignment (PHX-7 is blocked).
    assert [(s.issue, s.to_member) for s in workload.suggestions] == [("PHX-5", "Dev R")]


# ------------------------------------------------------------------ contradictions & risk


def test_jira_github_contradictions(snapshot) -> None:
    found = {(c.kind, c.issue) for c in find_contradictions(snapshot)}

    assert found == {("merged_but_not_done", "PHX-4"), ("done_but_pr_open", "PHX-6")}


def test_review_without_pr_is_detected(snapshot) -> None:
    snapshot.pulls = [p for p in snapshot.pulls if "PHX-3" not in p.issue_keys]

    assert ("review_without_pr", "PHX-3") in {
        (c.kind, c.issue) for c in find_contradictions(snapshot)
    }


def test_risk_assessment_is_explained(snapshot) -> None:
    forecast, workload = forecast_sprint(snapshot), compute_workload(snapshot)
    risk = assess_risk(snapshot, forecast, workload, find_contradictions(snapshot))
    factors = {f.name: f for f in risk.factors}

    assert risk.level == "high" and risk.score >= 60
    assert any("PHX-7" in e for e in factors["Overdue work"].evidence)
    assert factors["Blocked work"].evidence == ["PHX-3 blocked by PHX-7", "PHX-7 blocked"]
    assert "2 consecutive failing builds on main" in factors["Delivery pipeline"].evidence
    assert any(
        e == "PHX-5 due while Arjun M is on leave" for e in factors["Team capacity"].evidence
    )
    assert all(f.score <= f.max_score for f in risk.factors)


def test_healthy_project_scores_low() -> None:
    quiet = ProjectSnapshot(key="OK", name="Quiet", today=MONDAY)

    risk = assess_risk(quiet, None, compute_workload(quiet), [])

    assert (risk.score, risk.level) == (0, "low")


# ------------------------------------------------------------------ collector & report template


async def test_collector_reports_missing_integrations_as_warnings() -> None:
    settings = Settings(_env_file=None)
    snapshot = await SnapshotCollector(IntegrationHub(mode="live"), settings).collect(
        phx_project(), today=MONDAY
    )

    assert snapshot.issues == []
    assert any("Jira is not connected" in w for w in snapshot.warnings)
    assert any("GitHub is not connected" in w for w in snapshot.warnings)
    assert any("Keka is not connected" in w for w in snapshot.warnings)


async def test_collector_survives_a_failing_integration(snapshot) -> None:
    settings = Settings(_env_file=None, demo_mode=True)
    hub = build_integration_hub(settings)

    async def broken(*args, **kwargs):
        raise RuntimeError("keka down")

    hub.keka.get_leave_requests = broken
    result = await SnapshotCollector(hub, settings).collect(phx_project(), today=MONDAY)

    assert result.issues and result.pulls
    assert result.leave == []
    assert any("Keka leave unavailable" in w for w in result.warnings)


def test_week_bounds_and_template(snapshot) -> None:
    from backend.app.domains.analytics.results import ProjectHealth

    forecast, workload = forecast_sprint(snapshot), compute_workload(snapshot)
    contradictions = find_contradictions(snapshot)
    health = ProjectHealth(
        project="PHX", name="Phoenix", as_of=MONDAY,
        risk=assess_risk(snapshot, forecast, workload, contradictions),
        forecast=forecast, workload=workload, contradictions=contradictions,
        issue_counts={}, warnings=[],
    )  # fmt: skip

    text = render_template(health)

    assert week_bounds(date(2026, 11, 12)) == (MONDAY, date(2026, 11, 15))
    assert "Overall risk: **HIGH**" in text
    assert "off track" in text
    assert "Consider moving PHX-5 from Arjun M to Dev R" in text
