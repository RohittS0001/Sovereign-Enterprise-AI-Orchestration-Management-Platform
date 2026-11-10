"""Digital twin (Monte Carlo) and negotiation assistant."""

from datetime import date, timedelta

import pytest

from backend.app.core.config import Settings
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.negotiation import negotiate, recommend
from backend.app.domains.analytics.snapshot import (
    IssueFacts,
    LeaveFacts,
    Member,
    ProjectSnapshot,
    RunFacts,
    SprintFacts,
)
from backend.app.domains.analytics.twin import Scenario, simulate
from backend.app.integrations.hub import build_integration_hub
from backend.app.llm.fake import FakeLLM
from tests.support.analytics import phx_project

MONDAY = date(2026, 11, 9)


def issue(key, points=3.0, priority="Medium", category="todo", blocked_by=()) -> IssueFacts:
    return IssueFacts(key=key, summary=key, status=category, category=category, priority=priority,
                      assignee=None, assignee_id=None, due=None, points=points, issue_type="Story",
                      blocked_by=list(blocked_by))  # fmt: skip


def team_snapshot(issues, *, members=2, capacity=10.0, leave=(), runs=()) -> ProjectSnapshot:
    sprint = SprintFacts(1, "S1", "active", MONDAY, MONDAY + timedelta(days=11))  # 10 working days
    return ProjectSnapshot(
        key="T", name="Team", today=MONDAY, issues=list(issues), sprint=sprint,
        sprint_issue_keys={i.key for i in issues},
        members=[Member(f"m{n}", None, f"e{n}", capacity) for n in range(members)],
        leave=list(leave), runs=list(runs),
    )  # fmt: skip


@pytest.fixture
async def phx() -> ProjectSnapshot:
    settings = Settings(_env_file=None, demo_mode=True)
    return await SnapshotCollector(
        build_integration_hub(settings, demo_today=MONDAY), settings
    ).collect(phx_project(), today=MONDAY)


def test_same_seed_same_answer(phx) -> None:
    assert simulate(phx, seed=3) == simulate(phx, seed=3)


def test_nothing_left_means_done_today() -> None:
    snap = team_snapshot([issue("T-1", category="done")])
    result = simulate(snap)
    assert result.p50 == MONDAY and result.probability_by_target == 1.0


def test_easy_plan_is_likely_and_overloaded_plan_is_not() -> None:
    # 2 people x 1 point/day x 10 days = 20 points of capacity (efficiency 0.8 by default).
    easy = simulate(team_snapshot([issue(f"T-{n}") for n in range(3)]))  # 9 points
    hard = simulate(team_snapshot([issue(f"T-{n}") for n in range(15)]))  # 45 points

    assert easy.probability_by_target > 0.95
    assert hard.probability_by_target < 0.05
    assert easy.p50 <= easy.p80 <= easy.p95


def test_what_ifs_move_the_forecast_in_the_right_direction(phx) -> None:
    base = simulate(phx)
    more_people = simulate(phx, Scenario(add_people=2))
    less_scope = simulate(phx, Scenario(cut_points=8))
    more_leave = simulate(phx, Scenario(extra_leave_days=15))
    unblocked = simulate(phx, Scenario(resolve_blockers=True))

    assert more_people.p80 < base.p80 and less_scope.p80 < base.p80
    assert more_leave.p80 >= base.p80
    assert less_scope.remaining_points < base.remaining_points
    assert base.blocked_issues and not unblocked.blocked_issues


def test_drivers_explain_blockers_rework_and_leave() -> None:
    issues = [issue("T-1", blocked_by=["T-2"]), issue("T-2")]
    runs = [RunFacts("r", "main", "completed", "failure", MONDAY)] * 3
    leave = [LeaveFacts("e0", MONDAY, MONDAY + timedelta(days=2))]
    result = simulate(team_snapshot(issues, runs=runs, leave=leave))

    text = " ".join(result.drivers)
    assert "blocked (T-1)" in text and "CI runs failed" in text and "leave" in text
    assert result.rework_chance == 0.6  # capped


def test_unpointed_issues_count_as_the_median() -> None:
    snap = team_snapshot([issue("T-1", 2), issue("T-2", 4), issue("T-3", 0)])
    assert simulate(snap).remaining_points == 9.0


async def test_negotiation_recommends_the_least_disruptive_option_that_works() -> None:
    # 30 points, 20 of capacity: blockers aside, cutting the Low item or adding people helps.
    issues = [issue("T-1", 8, "High"), issue("T-2", 8, "High"), issue("T-3", 6, "Medium"),
              issue("T-4", 8, "Low")]  # fmt: skip
    plan = await negotiate(team_snapshot(issues, members=2, capacity=12))

    ids = [o.id for o in plan.options]
    assert "resolve_blockers" not in ids  # nothing is blocked
    assert {"phased_release", "cut_scope", "add_one", "move_date"} <= set(ids)
    assert plan.recommended == recommend(plan.options)
    chosen = next(o for o in plan.options if o.id == plan.recommended)
    assert chosen.meets_goal
    assert "| Option |" in plan.message_markdown and plan.generated_by_model is None
    cut = next(o for o in plan.options if o.id == "cut_scope")
    assert all(k in {"T-3", "T-4"} for k in cut.scenario.remove_issues)  # never High work


async def test_negotiation_message_can_come_from_the_reporting_model(phx) -> None:
    model = FakeLLM(["Deadline at risk: we recommend moving the date."])
    plan = await negotiate(phx, model=model)

    assert plan.message_markdown.startswith("Deadline at risk")
    prompt = model.calls[0]["messages"][1].content
    assert "<untrusted_data" in prompt  # numbers are wrapped as untrusted data


async def test_negotiation_falls_back_to_the_template_when_the_model_fails(phx) -> None:
    class Broken:
        model = "broken"

        async def chat(self, *args, **kwargs):
            raise RuntimeError("model down")

    plan = await negotiate(phx, model=Broken())
    assert plan.message_markdown.startswith("## PHX") and plan.generated_by_model is None
