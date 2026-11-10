"""Why is it late: days lost per cause, root causes through dependencies, claims vs data."""

from datetime import date, timedelta

import pytest

from backend.app.core.config import Settings
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.snapshot import (
    IssueFacts,
    LeaveFacts,
    Member,
    ProjectSnapshot,
    PullFacts,
    SprintFacts,
)
from backend.app.domains.delivery.delay import (
    DelayFacts,
    Note,
    StatusChange,
    analyze_delay,
    check_statement,
    collect_delay_facts,
    parse_history,
    parse_when,
    segments,
)
from backend.app.domains.delivery.roadmap import build_roadmap
from backend.app.integrations.hub import build_integration_hub
from tests.support.analytics import phx_project

MONDAY = date(2026, 11, 2)  # sprint start
TODAY = date(2026, 11, 9)  # the following Monday: 5 working days elapsed


def issue(
    key,
    *,
    status="In Progress",
    category="in_progress",
    priority="Medium",
    points=3.0,
    who="A",
    blocked_by=(),
    created=None,
) -> IssueFacts:
    return IssueFacts(
        key=key,
        summary=key,
        status=status,
        category=category,
        priority=priority,
        assignee=who,
        assignee_id=None,
        due=None,
        points=points,
        issue_type="Story",
        blocked_by=list(blocked_by),
        created=created,
    )


def snapshot(issues, **extra) -> ProjectSnapshot:
    sprint = SprintFacts(1, "S1", "active", MONDAY, MONDAY + timedelta(days=11))
    return ProjectSnapshot(
        key="T",
        name="Team",
        today=TODAY,
        issues=list(issues),
        sprint=sprint,
        sprint_issue_keys={i.key for i in issues},
        members=[Member("A", None, "e-a", 10), Member("B", None, "e-b", 10)],
        **extra,
    )


def move(day_offset, old, new) -> StatusChange:
    return StatusChange(MONDAY + timedelta(days=day_offset), "A", old, new)


# ------------------------------------------------------------------ parsing


def test_parse_jira_changelog_keeps_only_status_changes() -> None:
    raw = {
        "values": [
            {
                "created": "2026-11-04T10:00:00.000+0530",
                "author": {"displayName": "Arjun"},
                "items": [
                    {"field": "status", "fromString": "To Do", "toString": "In Progress"},
                    {"field": "assignee", "fromString": None, "toString": "Arjun"},
                ],
            },
            {"created": "2026-11-03", "author": "Sara", "items": [{"field": "priority"}]},
        ]
    }
    changes = parse_history(raw)
    assert [(c.at, c.author, c.to_status) for c in changes] == [
        (date(2026, 11, 4), "Arjun", "In Progress")
    ]


def test_segments_clip_to_the_sprint_and_count_working_days() -> None:
    segs = segments(
        issue("T-1", created=date(2026, 10, 20)),
        [move(-3, "To Do", "In Progress"), move(2, "In Progress", "Blocked")],
        MONDAY,
        TODAY,
    )
    assert [(s.status, s.start, s.days) for s in segs] == [
        ("In Progress", MONDAY, 2),
        ("Blocked", date(2026, 11, 4), 3),  # Wed, Thu, Fri
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11-14", date(2026, 11, 14)),
        ("14 Nov", date(2026, 11, 14)),
        ("Nov 14th", date(2026, 11, 14)),
        ("Friday", date(2026, 11, 13)),
        ("tomorrow", date(2026, 11, 10)),
        ("12 Jan", date(2027, 1, 12)),
        ("soon", None),
    ],
)
def test_parse_when_relative_to_when_it_was_said(text, expected) -> None:
    assert parse_when(text, TODAY) == expected


# ------------------------------------------------------------------ attribution


def test_dependency_chain_is_charged_to_the_root_cause() -> None:
    blocker = issue("T-1", status="Blocked", priority="Highest")
    waiting = issue("T-2", blocked_by=["T-1"], who="B")
    facts = DelayFacts(
        history={
            "T-1": [move(0, "To Do", "In Progress"), move(2, "In Progress", "Blocked")],
            "T-2": [move(0, "To Do", "In Review"), move(1, "In Review", "In Progress")],
        },
        notes=[
            Note(MONDAY, "A", "Vendor regression on their side", "jira comment", "T-1", ["T-1"])
        ],
    )
    report = analyze_delay(snapshot([blocker, waiting]), facts)
    top = report.losses[0]

    assert top.cause == "blocked_external" and top.issues == ["T-1", "T-2"]
    assert top.days == 3 + 4  # T-1 blocked Wed–Fri; T-2 waiting since Tue
    assert not any(loss.cause == "rework" for loss in report.losses)
    assert "Main reason: waiting on an external party" in report.headline


def test_late_start_review_wait_rework_scope_leave_and_internal_blocks() -> None:
    urgent = issue("T-1", priority="High", category="in_progress")
    reworked = issue("T-2")
    added = issue("T-3", created=date(2026, 11, 4), points=5, category="todo", status="To Do")
    internal = issue("T-4", status="Blocked", who="B")
    pull = PullFacts("acme/x", 7, "T-1 change", "open", False, None, MONDAY, None, [], [], ["T-1"])
    facts = DelayFacts(
        history={
            "T-1": [move(3, "To Do", "In Progress")],  # started Thursday
            "T-2": [
                move(0, "To Do", "In Review"),
                move(1, "In Review", "In Progress"),
                move(3, "In Progress", "In Review"),
            ],
            "T-4": [move(4, "In Progress", "Blocked")],
        }
    )
    leave = [LeaveFacts("e-b", date(2026, 11, 10), date(2026, 11, 11))]
    report = analyze_delay(
        snapshot([urgent, reworked, added, internal], pulls=[pull], leave=leave), facts
    )
    days = {loss.cause: loss.days for loss in report.losses}

    assert days["started_late"] == 2  # 3 working days in, minus a day of grace
    assert days["review_wait"] == 4  # open 5 working days, minus the one-day agreement
    assert days["rework"] == 2  # sent back Tuesday, back in review Thursday
    assert days["scope_added"] == pytest.approx(2.5)  # 5 points at 2 points/day
    assert days["blocked_internal"] == 1
    assert days["leave"] == 2
    assert sum(loss.share for loss in report.losses) == pytest.approx(1, abs=0.02)


def test_nothing_lost_says_so() -> None:
    report = analyze_delay(snapshot([issue("T-1", category="done", status="Done")]), DelayFacts())
    assert report.losses == [] and report.headline == "No time lost found in the data."


# ------------------------------------------------------------------ claims


def test_claims_are_checked_against_the_data_on_the_day_they_were_made() -> None:
    blocked = issue("T-1", status="Blocked")
    facts = DelayFacts(
        history={"T-1": [move(1, "In Progress", "Blocked")]},
        notes=[
            Note(date(2026, 11, 2), "pm", "No blockers so far.", "email", "update 1"),
            Note(
                date(2026, 11, 5), "pm", "Still no blockers. We are on track.", "email", "update 2"
            ),
        ],
    )
    snap = snapshot([blocked])
    report = analyze_delay(snap, facts, build_roadmap(snap))
    verdicts = {(c.said_on, c.claim): c.verdict for c in report.claims}

    assert verdicts[(date(2026, 11, 2), "No blockers so far.")] == "supported"  # true that day
    assert verdicts[(date(2026, 11, 5), "Still no blockers.")] == "contradicted"
    assert report.claims[0].verdict == "contradicted"  # problems first


def test_check_statement_compares_promises_with_the_projection() -> None:
    snap = snapshot([issue("T-1", points=20, who="A", category="todo", status="To Do")])
    road = build_roadmap(snap)
    late = check_statement("T-1 will be done by Friday", snap, DelayFacts(), road)
    vague = check_statement("Things are fine", snap, DelayFacts(), road)

    assert late[0].verdict == "contradicted" and "working day(s) after" in late[0].reality[0]
    assert vague[0].verdict == "unverifiable"


# ------------------------------------------------------------------ demo end to end


async def test_phx_story_from_demo_data() -> None:
    settings = Settings(_env_file=None, demo_mode=True)
    hub = build_integration_hub(settings, demo_today=TODAY)
    snap = await SnapshotCollector(hub, settings).collect(phx_project(), today=TODAY)
    facts = await collect_delay_facts(hub, snap)
    without_email = await collect_delay_facts(hub, snap, include_email=False)
    report = analyze_delay(snap, facts, build_roadmap(snap))

    assert set(facts.history) >= {"PHX-1", "PHX-3", "PHX-7"}
    assert any(n.source == "email" for n in facts.notes)
    assert not any(n.source == "email" for n in without_email.notes)
    assert report.losses[0].cause == "blocked_external"
    assert report.slip_days > 0
    manager = [c for c in report.claims if c.said_by == "priya.n@acme.example"]
    assert manager and all(c.verdict == "contradicted" for c in manager)
