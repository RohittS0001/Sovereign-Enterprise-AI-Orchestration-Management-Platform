"""Correctness regressions: twin blockers, sprint-end forecast, remaining capacity, currencies,
portable configuration paths."""

import re
from datetime import date

import pytest

from backend.app.domains.analytics.evm import earned_value
from backend.app.domains.analytics.forecast import forecast_sprint
from backend.app.domains.analytics.snapshot import (
    IssueFacts,
    LeaveFacts,
    Member,
    ProjectSnapshot,
    SprintFacts,
)
from backend.app.domains.analytics.twin import Scenario, _prepare
from backend.app.domains.analytics.workload import compute_workload
from backend.app.domains.cloud.service import _merge_costs
from backend.app.integrations.connectors.base import BudgetLine, CostLine, CostReport

# A two-week sprint: Mon 2 Nov – Fri 13 Nov 2026 = 10 working days.
SPRINT = SprintFacts(
    id=1, name="S1", state="active", start=date(2026, 11, 2), end=date(2026, 11, 13)
)


def issue(key: str, *, status: str = "To Do", category: str = "todo", points: float = 3,
          assignee: str | None = "Ana", assignee_id: str | None = None,
          blocked_by: list[str] | None = None) -> IssueFacts:  # fmt: skip
    return IssueFacts(
        key=key, summary=key, status=status, category=category, priority="Medium",
        assignee=assignee, assignee_id=assignee_id, due=None, points=points, issue_type="Task",
        blocked_by=blocked_by or [],
    )  # fmt: skip


def snap(today: date, issues: list[IssueFacts], **extra) -> ProjectSnapshot:
    s = ProjectSnapshot(
        key="PHX", name="Phoenix", today=today, issues=issues, sprint=SPRINT, **extra
    )
    s.sprint_issue_keys = {i.key for i in issues}
    return s


# ------------------------------------------------------------------ 12. twin blockers


def test_a_blocked_status_without_links_is_blocked_in_the_twin() -> None:
    members = [Member("Ana", None, None, 10)]
    s = snap(
        date(2026, 11, 9),
        [issue("PHX-7", status="Blocked", category="in_progress"), issue("PHX-8")],
        members=members,
    )
    work = {w.key: w.blocked for w in _prepare(s, Scenario()).work}
    assert work == {"PHX-7": True, "PHX-8": False}  # PHX-7: status only, no blocked_by link
    resolved = {w.key: w.blocked for w in _prepare(s, Scenario(resolve_blockers=True)).work}
    assert resolved["PHX-7"] is False


def test_a_link_to_finished_work_no_longer_blocks() -> None:
    s = snap(
        date(2026, 11, 9),
        [issue("A", blocked_by=["B"]), issue("B", category="done", status="Done")],
        members=[Member("Ana", None, None, 10)],
    )
    assert {w.key: w.blocked for w in _prepare(s, Scenario()).work} == {"A": False}


async def test_demo_phx7_counts_as_blocked() -> None:
    from backend.app.core.config import Settings
    from backend.app.domains.analytics.collector import SnapshotCollector
    from backend.app.integrations.hub import build_integration_hub
    from tests.support.analytics import phx_project

    today = date(2026, 11, 9)
    settings = Settings(_env_file=None, demo_mode=True)
    collector = SnapshotCollector(build_integration_hub(settings, demo_today=today), settings)
    snapshot = await collector.collect(phx_project(), today=today)
    blocked = {w.key for w in _prepare(snapshot, Scenario()).work if w.blocked}
    assert "PHX-7" in blocked


# ------------------------------------------------------------------ 13. forecast boundaries


@pytest.mark.parametrize(
    ("today", "elapsed", "left"),
    [
        (date(2026, 11, 2), 0, 10),  # first day: nothing has elapsed yet
        (date(2026, 11, 13), 9, 1),  # final day: still running, today not over
        (date(2026, 11, 14), 10, 0),  # Saturday after: the final day counts
        (date(2026, 11, 16), 10, 0),  # first working day after the sprint
        (date(2026, 10, 30), 0, 10),  # before it starts
    ],
)
def test_forecast_counts_the_final_sprint_day_once_it_is_over(today, elapsed, left) -> None:
    s = snap(today, [issue("A", category="done", status="Done", points=5), issue("B", points=5)])
    f = forecast_sprint(s)
    assert (f.working_days_elapsed, f.working_days_left) == (elapsed, left)


def test_completed_sprint_burn_rate_uses_all_its_days() -> None:
    s = snap(
        date(2026, 11, 16),
        [issue("A", category="done", status="Done", points=10), issue("B", points=5)],
    )
    f = forecast_sprint(s)
    assert f.burn_rate_per_day == 1.0 and f.projected_points == 10  # 10 points / 10 days


# ------------------------------------------------------------------ 14. remaining capacity


@pytest.mark.parametrize(
    ("today", "effective"),
    [
        (date(2026, 11, 2), 10.0),  # start: the whole sprint ahead
        (date(2026, 11, 9), 5.0),  # middle: 5 of 10 working days left
        (date(2026, 11, 13), 1.0),  # last day: 1 of 10 left
    ],
)
def test_capacity_scales_with_the_sprint_days_left(today, effective) -> None:
    s = snap(today, [issue("A", points=4)], members=[Member("Ana", None, None, 10)])
    [ana] = compute_workload(s).members
    assert ana.effective_capacity == effective


def test_leave_is_counted_once_inside_the_remaining_window() -> None:
    member = Member("Ana", "acc-1", "K1", 10)
    leave = [
        LeaveFacts("K1", date(2026, 11, 3), date(2026, 11, 4)),  # already past: ignored
        LeaveFacts("K1", date(2026, 11, 10), date(2026, 11, 11)),  # 2 of the 5 days left
    ]
    s = snap(date(2026, 11, 9), [issue("A", points=2)], members=[member], leave=leave)
    [ana] = compute_workload(s).members
    assert ana.leave_days == 2 and ana.availability == 0.6
    assert ana.effective_capacity == 3.0  # 10 × 5/10 × 0.6


# ------------------------------------------------------------------ 15. EVM currencies


def test_evm_refuses_to_mix_currencies() -> None:
    s = snap(
        date(2026, 11, 9),
        [issue("A", category="done", status="Done", points=5), issue("B", points=5)],
    )
    s.finance = [
        BudgetLine(source="netsuite", project="PHX", budget=10_000, actual=6_000, currency="USD"),
        BudgetLine(source="zoho", project="PHX", budget=800_000, actual=100_000, currency="INR"),
    ]
    value = earned_value(s)
    assert value.cpi is None and value.budget_at_completion is None and value.actual_cost is None
    assert value.estimate_at_completion is None and value.currency is None
    assert value.by_currency == {
        "INR": {"budget": 800_000, "actual": 100_000},
        "USD": {"budget": 10_000, "actual": 6_000},
    }
    assert "different currencies" in value.note and "INR" in value.note


def test_evm_single_currency_still_computes() -> None:
    s = snap(
        date(2026, 11, 9),
        [issue("A", category="done", status="Done", points=5), issue("B", points=5)],
    )
    s.finance = [
        BudgetLine(source="netsuite", project="PHX", budget=10_000, actual=6_000, currency="USD")
    ]
    value = earned_value(s)
    assert value.currency == "USD" and value.earned_value == 5000 and value.cpi == 0.83


# ------------------------------------------------------------------ 16. cloud cost currencies


def report(source: str, currency: str, total: float, forecast: float | None = None) -> CostReport:
    return CostReport(
        source=source, currency=currency, period_start=date(2026, 11, 1), period_end=date(2026, 11, 9),
        total=total, forecast_month_end=forecast, by_service=[CostLine(service="Compute", amount=total)],
    )  # fmt: skip


def test_cloud_costs_in_different_currencies_are_not_added() -> None:
    cost = _merge_costs(
        [report("aws", "USD", 120.0, 300.0), report("azure", "INR", 9000.0, 20000.0)]
    )
    assert cost.currency is None and cost.month_to_date is None and cost.forecast_month_end is None
    assert cost.by_currency == {"INR": 9000.0, "USD": 120.0}
    assert cost.cloud_currency == {"aws": "USD", "azure": "INR"}
    assert {line.service for line in cost.by_service} == {"Compute (USD)", "Compute (INR)"}


def test_cloud_costs_in_one_currency_are_summed() -> None:
    cost = _merge_costs([report("aws", "USD", 120.0, 300.0), report("azure", "USD", 80.0, 100.0)])
    assert (cost.currency, cost.month_to_date, cost.forecast_month_end) == ("USD", 200.0, 400.0)
    assert cost.by_currency == {"USD": 200.0}


# ------------------------------------------------------------------ 18. portable paths


def test_project_root_is_the_repository() -> None:
    from backend.app.core.config import PROJECT_ROOT, Settings

    assert (PROJECT_ROOT / "pyproject.toml").exists()
    resolved = Settings(_env_file=None, gmail_token_file="token.json").gmail_token_file
    assert resolved == PROJECT_ROOT / "token.json"


def test_connections_template_has_no_machine_specific_paths() -> None:
    from scripts.connections import _default, env_template

    assert _default("gmail_credentials_file") == "credentials.json"
    assert _default("gmail_token_file") == "token.json"
    template = env_template()
    assert not re.search(r"[A-Za-z]:\\", template)  # Windows drive paths
    assert not re.search(r"default: /", template)  # POSIX absolute paths
