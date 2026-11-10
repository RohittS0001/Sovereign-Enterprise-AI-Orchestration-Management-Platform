"""Roadmap: dependency graph, resource-aware schedule and critical path.

Every open issue becomes a task whose duration is its remaining points divided by its
assignee's daily capacity (sprint capacity / sprint working days). Edges come from two
places:

- **dependencies** — Jira "is blocked by" links (blocker → blocked);
- **same assignee** — one person works one thing at a time, so each person's issues are
  chained in a priority-respecting topological order (critical-chain style).

A forward/backward pass (CPM) gives earliest/latest start, slack and the critical path:
the zero-slack chain that sets the finish date. Days are working days mapped onto the
calendar from ``snapshot.today``. Dependency cycles are reported and broken, never fatal.
Pure: the same snapshot always gives the same roadmap.
"""

import heapq
import statistics
from collections import defaultdict
from datetime import date, timedelta

from pydantic import BaseModel, Field

from backend.app.domains.analytics.forecast import working_days
from backend.app.domains.analytics.snapshot import IssueFacts, ProjectSnapshot

PRIORITY_RANK = {
    "highest": 0,
    "blocker": 0,
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "lowest": 4,
    "trivial": 4,
}
DEFAULT_SPRINT_DAYS = 10
DEFAULT_DAILY_POINTS = 1.0
EPSILON = 1e-6


class RoadmapItem(BaseModel):
    key: str
    summary: str
    assignee: str | None
    status: str
    points: float
    duration_days: float = Field(description="Working days of effort left")
    start: date
    finish: date
    start_day: float
    finish_day: float
    slack_days: float
    critical: bool
    depends_on: list[str] = Field(description="Blocking issues (Jira links)")
    after: list[str] = Field(description="Earlier work by the same assignee")
    due: date | None = None
    late_by_days: int | None = Field(default=None, description="Calendar days past the due date")


class Roadmap(BaseModel):
    project: str
    as_of: date
    finish: date
    total_working_days: float
    critical_path: list[str]
    items: list[RoadmapItem]
    external_blockers: dict[str, list[str]] = Field(
        description="Issue → blockers outside this project (not scheduled)"
    )
    cycles: list[list[str]]
    late_vs_sprint: bool
    sprint_end: date | None
    notes: list[str]


def add_working_days(start: date, days: float) -> date:
    """The calendar date ``days`` working days after ``start`` (fractions round up)."""
    current = start
    while current.weekday() >= 5:
        current += timedelta(days=1)
    whole = int(-(-days // 1)) if days > EPSILON else 0  # ceil
    remaining = max(0, whole - 1) if whole else 0
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _daily_rates(snapshot: ProjectSnapshot) -> tuple[dict[str, float], float]:
    sprint = snapshot.sprint
    days = DEFAULT_SPRINT_DAYS
    if sprint and sprint.start and sprint.end:
        days = max(1, working_days(sprint.start, sprint.end))
    rates = {m.name: m.capacity_points / days for m in snapshot.members if m.capacity_points > 0}
    default = statistics.fmean(rates.values()) if rates else DEFAULT_DAILY_POINTS
    return rates, default


def _topological(
    keys: list[str], preds: dict[str, set[str]], rank: dict[str, tuple]
) -> tuple[list[str], list[list[str]]]:
    """Kahn's algorithm with a priority heap; cycles are broken and reported."""
    indegree = {k: len(preds[k]) for k in keys}
    succs: dict[str, set[str]] = defaultdict(set)
    for key in keys:
        for p in preds[key]:
            succs[p].add(key)
    heap = [(rank[k], k) for k in keys if indegree[k] == 0]
    heapq.heapify(heap)
    order: list[str] = []
    cycles: list[list[str]] = []
    remaining = set(keys)
    while remaining:
        if not heap:  # a cycle: break it at its highest-priority member
            stuck = sorted(remaining, key=lambda k: rank[k])
            cycles.append(stuck)
            first = stuck[0]
            for p in list(preds[first]):
                if p in remaining:
                    preds[first].discard(p)
                    succs[p].discard(first)
            indegree[first] = 0
            heapq.heappush(heap, (rank[first], first))
        _, key = heapq.heappop(heap)
        if key not in remaining:
            continue
        remaining.discard(key)
        order.append(key)
        for s in sorted(succs[key]):
            if s in remaining:
                indegree[s] -= 1
                if indegree[s] == 0:
                    heapq.heappush(heap, (rank[s], s))
    return order, cycles


def build_roadmap(snapshot: ProjectSnapshot) -> Roadmap:
    issues = [i for i in (snapshot.sprint_issues or snapshot.issues) if i.is_open]
    by_key: dict[str, IssueFacts] = {i.key: i for i in issues}
    keys = sorted(by_key)
    notes: list[str] = []

    pointed = [i.points for i in issues if i.points > 0]
    median_points = statistics.median(pointed) if pointed else 3.0
    if len(pointed) < len(issues):
        notes.append(f"Unestimated issues count as the median ({median_points:g} points).")
    rates, default_rate = _daily_rates(snapshot)
    if any(i.category == "in_progress" for i in issues):
        notes.append("In-progress issues are assumed to be half done.")
    if not rates:
        notes.append(f"No team capacity configured; assuming {default_rate:g} point(s)/day.")

    def duration(issue: IssueFacts) -> float:
        points = issue.points if issue.points > 0 else median_points
        rate = rates.get(issue.assignee or "", default_rate)
        remaining = points * (0.5 if issue.category == "in_progress" else 1.0)
        return round(remaining / rate, 2)

    deps: dict[str, set[str]] = {k: set() for k in keys}
    external: dict[str, list[str]] = {}
    for key in keys:
        for blocker in by_key[key].blocked_by:
            if blocker in by_key:
                deps[key].add(blocker)
            elif not any(blocker == i.key and not i.is_open for i in snapshot.issues):
                external.setdefault(key, []).append(blocker)
    if external:
        notes.append("Blockers outside this project are not scheduled; their dates are unknown.")

    rank = {
        k: (PRIORITY_RANK.get(by_key[k].priority.lower(), 2), by_key[k].due or date.max, k)
        for k in keys
    }
    preds = {k: set(v) for k, v in deps.items()}
    order, cycles = _topological(keys, preds, rank)
    for cycle in cycles:
        notes.append(f"Dependency cycle broken among: {', '.join(cycle)}.")
    deps = {k: {p for p in deps[k] if p in preds[k]} for k in keys}  # cycle edges removed

    # List scheduling: repeatedly start the ready issue that can begin earliest, unless doing
    # so would push back a higher-priority issue of the same person (then that one goes first).
    length = {k: duration(by_key[k]) for k in keys}
    after: dict[str, set[str]] = {k: set() for k in keys}
    earliest_start: dict[str, float] = {}
    earliest_finish: dict[str, float] = {}
    person_free: dict[str, float] = {}
    person_last: dict[str, str] = {}
    schedule: list[str] = []
    pending = set(order)

    def can_start(key: str) -> float:
        ready = max((earliest_finish[p] for p in deps[key]), default=0.0)
        person = by_key[key].assignee
        return max(ready, person_free.get(person, 0.0)) if person else ready

    while pending:
        ready = [k for k in pending if all(p in earliest_finish for p in deps[k])]
        key = min(ready, key=lambda k: (can_start(k), rank[k]))
        person = by_key[key].assignee
        if person:
            first = can_start(key) + length[key]
            urgent = [
                h
                for h in ready
                if h != key
                and by_key[h].assignee == person
                and rank[h] < rank[key]
                and first > can_start(h)
            ]
            if urgent:
                key = min(urgent, key=lambda k: (rank[k], can_start(k)))
        earliest_start[key] = can_start(key)
        earliest_finish[key] = earliest_start[key] + length[key]
        if person := by_key[key].assignee:
            if person in person_last:
                after[key].add(person_last[person])
            person_last[person], person_free[person] = key, earliest_finish[key]
        pending.discard(key)
        schedule.append(key)
    order = schedule
    if any(i.assignee is None for i in issues):
        notes.append("Unassigned issues are scheduled as soon as their dependencies allow.")
    preds_all = {k: deps[k] | after[k] for k in keys}
    end = max(earliest_finish.values(), default=0.0)

    succs: dict[str, set[str]] = defaultdict(set)
    for key in keys:
        for p in preds_all[key]:
            succs[p].add(key)
    latest_finish: dict[str, float] = {}
    for key in reversed(order):
        latest_finish[key] = min((latest_finish[s] - length[s] for s in succs[key]), default=end)
    slack = {k: round(latest_finish[k] - earliest_finish[k], 2) for k in keys}

    critical_path: list[str] = []
    if keys:
        current = max(keys, key=lambda k: (earliest_finish[k], -rank[k][0]))
        while current:
            critical_path.append(current)
            candidates = [
                p
                for p in preds_all[current]
                if abs(earliest_finish[p] - earliest_start[current]) < EPSILON
            ]
            current = min(candidates, key=lambda p: rank[p]) if candidates else None
        critical_path.reverse()
    on_path = set(critical_path)

    today = snapshot.today
    items = []
    for key in order:
        issue = by_key[key]
        finish = add_working_days(today, earliest_finish[key])
        items.append(
            RoadmapItem(
                key=key,
                summary=issue.summary,
                assignee=issue.assignee,
                status=issue.status,
                points=issue.points,
                duration_days=length[key],
                start=add_working_days(today, earliest_start[key] + EPSILON),
                finish=finish,
                start_day=round(earliest_start[key], 2),
                finish_day=round(earliest_finish[key], 2),
                slack_days=max(0.0, slack[key]),
                critical=key in on_path,
                depends_on=sorted(deps[key]),
                after=sorted(after[key]),
                due=issue.due,
                late_by_days=(finish - issue.due).days
                if issue.due and finish > issue.due
                else None,
            )
        )

    finish_date = add_working_days(today, end)
    sprint_end = snapshot.sprint.end if snapshot.sprint else None
    return Roadmap(
        project=snapshot.key,
        as_of=today,
        finish=finish_date,
        total_working_days=round(end, 2),
        critical_path=critical_path,
        items=items,
        external_blockers=external,
        cycles=cycles,
        late_vs_sprint=bool(sprint_end and finish_date > sprint_end),
        sprint_end=sprint_end,
        notes=notes,
    )
