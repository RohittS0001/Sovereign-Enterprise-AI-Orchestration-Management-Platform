"""Team workload vs. capacity (leave-aware) and reassignment suggestions."""

from datetime import date, timedelta

from backend.app.analytics.metrics.results import MemberLoad, Reassignment, Workload
from backend.app.analytics.snapshot import IssueFacts, Member, ProjectSnapshot
from backend.app.analytics.trends.forecast import working_days

OVERLOADED = 1.1
UNDERUSED = 0.6


def _leave_days(
    snapshot: ProjectSnapshot, member: Member, start: date, end: date
) -> tuple[int, list[tuple[date, date]]]:
    periods = []
    days = 0
    for leave in snapshot.leave:
        if leave.employee_id != member.keka_employee_id:
            continue
        overlap_start, overlap_end = max(leave.start, start), min(leave.end, end)
        if overlap_start <= overlap_end:
            days += working_days(overlap_start, overlap_end)
            periods.append((leave.start, leave.end))
    return days, periods


def _assigned(issue: IssueFacts, member: Member) -> bool:
    if member.jira_account_id and issue.assignee_id:
        return issue.assignee_id == member.jira_account_id
    return bool(issue.assignee) and issue.assignee == member.name


def compute_workload(snapshot: ProjectSnapshot) -> Workload:
    today = snapshot.today
    period_end = (
        snapshot.sprint.end
        if snapshot.sprint and snapshot.sprint.end and snapshot.sprint.end >= today
        else today + timedelta(days=13)
    )
    period_days = max(1, working_days(today, period_end))
    scope = snapshot.sprint_issues or snapshot.issues
    open_issues = [i for i in scope if i.is_open]

    loads: list[MemberLoad] = []
    assigned_keys: set[str] = set()
    member_issues: dict[str, list[IssueFacts]] = {}
    for member in snapshot.members:
        mine = [i for i in open_issues if _assigned(i, member)]
        member_issues[member.name] = mine
        assigned_keys.update(i.key for i in mine)
        leave_days, periods = _leave_days(snapshot, member, today, period_end)
        availability = max(0.0, 1 - leave_days / period_days)
        effective = member.capacity_points * availability
        points = sum(i.points for i in mine)
        ratio = points / effective if effective else (float("inf") if points else 0.0)
        status = (
            "overloaded" if ratio > OVERLOADED else "underused" if ratio < UNDERUSED else "balanced"
        )
        # At risk: the assignee is away at some point between today and the due date.
        at_risk = [
            i.key
            for i in mine
            if i.due and any(start <= i.due and end >= today for start, end in periods)
        ]
        loads.append(
            MemberLoad(
                name=member.name,
                open_issues=[i.key for i in mine],
                open_points=points,
                capacity_points=member.capacity_points,
                availability=round(availability, 2),
                effective_capacity=round(effective, 1),
                load_ratio=round(ratio, 2) if ratio != float("inf") else 99.0,
                status=status,
                leave_days=leave_days,
                at_risk_issues=at_risk,
            )
        )

    unassigned = [i.key for i in open_issues if not i.assignee]
    return Workload(
        period_end=period_end,
        members=loads,
        unassigned_open=unassigned,
        suggestions=_suggest(loads, member_issues),
    )


def _suggest(
    loads: list[MemberLoad], member_issues: dict[str, list[IssueFacts]]
) -> list[Reassignment]:
    """Greedy: move the lowest-priority, not-started items from overloaded to underused people."""
    spare = {
        load.name: load.effective_capacity - load.open_points
        for load in loads
        if load.status == "underused" and load.effective_capacity > load.open_points
    }
    suggestions: list[Reassignment] = []
    for load in sorted(loads, key=lambda m: -m.load_ratio):
        if load.status != "overloaded":
            continue
        excess = load.open_points - load.effective_capacity
        candidates = sorted(
            (i for i in member_issues[load.name] if i.category == "todo" and not i.is_blocked),
            key=lambda i: (i.priority_weight, i.points),
        )
        for issue in candidates:
            if excess <= 0:
                break
            target = next(
                (
                    name
                    for name, room in sorted(spare.items(), key=lambda s: -s[1])
                    if room >= issue.points
                ),
                None,
            )
            if target is None:
                continue
            spare[target] -= issue.points
            excess -= issue.points
            reason = f"{load.name} is at {load.load_ratio:.0%} of capacity" + (
                f" with {load.leave_days} leave day(s)" if load.leave_days else ""
            )
            suggestions.append(
                Reassignment(
                    issue=issue.key,
                    points=issue.points,
                    from_member=load.name,
                    to_member=target,
                    reason=reason,
                )
            )
    return suggestions
