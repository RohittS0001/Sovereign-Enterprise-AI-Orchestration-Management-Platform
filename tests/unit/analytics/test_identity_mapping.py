"""Who an issue and a leave record belong to: Jira account id first, names only when unique."""

from datetime import date

from backend.app.core.config import Settings
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.snapshot import (
    IssueFacts,
    Member,
    ProjectSnapshot,
    SprintFacts,
)
from backend.app.domains.analytics.workload import compute_workload, owner_of
from backend.app.integrations.connectors.base import LeaveRecord

SPRINT = SprintFacts(
    id=1, name="S1", state="active", start=date(2026, 11, 2), end=date(2026, 11, 13)
)


def issue(key: str, assignee: str | None, assignee_id: str | None = None) -> IssueFacts:
    return IssueFacts(key=key, summary=key, status="To Do", category="todo", priority="Medium",
                      assignee=assignee, assignee_id=assignee_id, due=None, points=3,
                      issue_type="Task")  # fmt: skip


def test_jira_account_id_wins_over_the_display_name() -> None:
    priya_a = Member("Priya N", "acc-1", "K1", 10)
    priya_b = Member("Priya N", "acc-2", "K2", 10)
    members = [priya_a, priya_b]
    assert owner_of(issue("A", "Priya N", "acc-2"), members) is priya_b
    assert owner_of(issue("B", "Priya N", "acc-1"), members) is priya_a


def test_duplicate_names_without_ids_are_charged_to_nobody() -> None:
    members = [Member("Sam K", None, None, 10), Member("Sam K", None, None, 10)]
    assert owner_of(issue("A", "Sam K"), members) is None
    s = ProjectSnapshot(key="P", name="P", today=date(2026, 11, 2), issues=[issue("A", "Sam K")],
                        sprint=SPRINT, members=members)  # fmt: skip
    s.sprint_issue_keys = {"A"}
    assert [m.open_points for m in compute_workload(s).members] == [0, 0]  # not double-counted


def test_names_match_case_and_space_insensitively_and_unknowns_stay_unassigned() -> None:
    dev = Member("Dev  R", None, None, 10)
    assert owner_of(issue("A", "dev r"), [dev]) is dev
    assert owner_of(issue("B", "Someone Else"), [dev]) is None
    assert owner_of(issue("C", None), [dev]) is None


def test_a_name_never_lands_on_a_member_tied_to_another_account() -> None:
    meera = Member("Meera S", "acc-9", None, 10)
    # Same display name, different Jira account (e.g. a contractor): not Meera's work.
    assert owner_of(issue("A", "Meera S", "acc-other"), [meera]) is None
    assert owner_of(issue("B", "Meera S"), [meera]) is meera  # no id on the issue: by name


class HrSource:
    def __init__(self, records):
        self.records = records

    async def list_leave(self, start, end):
        return self.records


class Hub:
    jira = github = keka = email = None

    def __init__(self, hr):
        self.hr = hr

    def by_category(self, category):
        return {"bamboohr": self.hr} if category == "hr" else {}


async def test_hr_leave_by_id_and_never_by_an_ambiguous_name() -> None:
    members = [
        Member("Sam K", "acc-1", None, 10, hr_employee_id="H1"),
        Member("Sam K", "acc-2", None, 10, hr_employee_id="H2"),
        Member("Ana P", "acc-3", None, 10),
    ]
    records = [
        LeaveRecord(source="bamboohr", employee_id="H2", name="Sam K",
                    start=date(2026, 11, 4), end=date(2026, 11, 5)),
        LeaveRecord(source="bamboohr", employee_id="X9", name="Sam K",  # unknown id, shared name
                    start=date(2026, 11, 6), end=date(2026, 11, 6)),
        LeaveRecord(source="bamboohr", employee_id="X7", name="ana  p",
                    start=date(2026, 11, 9), end=date(2026, 11, 9)),
    ]  # fmt: skip
    settings = Settings(_env_file=None)
    collector = SnapshotCollector(Hub(HrSource(records)), settings)
    snapshot = ProjectSnapshot(key="P", name="P", today=date(2026, 11, 2), sprint=SPRINT,
                               members=members)  # fmt: skip

    await collector._hr(snapshot)

    assert [(lv.employee_id, lv.start.day) for lv in snapshot.leave] == [
        ("H2", 4),
        ("name:ana p", 9),
    ]
    assert any("several members share it" in w for w in snapshot.warnings)
