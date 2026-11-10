"""Project digital twin: Monte Carlo delivery simulation and what-if scenarios.

Each trial plays the remaining work forward one working day at a time:

- **Capacity** per person per day = sprint capacity / sprint working days, zero on leave
  days, scaled by the team's observed *efficiency* (points actually burned vs. capacity
  so far) and day-to-day noise (log-normal).
- **Blocked** issues cannot progress until unblocked (a daily chance).
- **Rework**: when an issue finishes, the main branch's recent CI failure rate is the
  chance it needs extra effort.
- Work is taken in priority order; unpointed issues count as the median pointed issue.

The result is a distribution of completion dates (P50/P80/P95), the probability of
finishing by a target date, and the drivers behind it. Scenarios change the inputs:
add people (with ramp-up), cut scope, extra leave, or a different target date.
Pure and seeded: the same snapshot and seed always give the same answer.
"""

import math
import random
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel, Field

from backend.app.domains.analytics.forecast import working_days
from backend.app.domains.analytics.snapshot import IssueFacts, ProjectSnapshot

PRIORITY_ORDER = {"highest": 0, "blocker": 0, "critical": 0, "high": 1, "medium": 2,
                  "low": 3, "lowest": 4, "trivial": 4}  # fmt: skip
DAILY_UNBLOCK_CHANCE = 0.2
REWORK_SHARE = 0.3  # extra effort when an issue bounces back
NOISE_SIGMA = 0.35
RAMP_UP_DAYS = 5  # new people work at half speed at first
HORIZON_WORKING_DAYS = 120


class Scenario(BaseModel):
    name: str = Field(default="baseline", max_length=60)
    add_people: int = Field(default=0, ge=0, le=20)
    cut_points: float = Field(default=0, ge=0, description="Remove lowest-priority scope")
    remove_issues: list[str] = Field(default_factory=list, max_length=200)
    extra_leave_days: int = Field(default=0, ge=0, le=60, description="Team-wide lost days")
    resolve_blockers: bool = Field(default=False, description="Assume blockers clear today")
    target_date: date | None = None


class SimulationResult(BaseModel):
    scenario: str
    target_date: date | None
    probability_by_target: float | None
    p50: date | None
    p80: date | None
    p95: date | None
    finish_rate: float = Field(description="Share of trials that finished within the horizon")
    remaining_points: float
    remaining_issues: int
    efficiency: float
    daily_capacity: float
    blocked_issues: list[str]
    rework_chance: float
    histogram: list[tuple[date, int]]
    trials: int
    drivers: list[str]


@dataclass
class _Work:
    key: str
    points: float
    blocked: bool
    rank: int
    reworked: bool = False


@dataclass
class _Inputs:
    today: date
    work: list[_Work]
    member_days: list[list[float]]  # per member: capacity per working day index
    efficiency: float
    rework_chance: float
    target: date | None
    added_people: int = 0
    person_day: float = 0.0
    dates: list[date] = field(default_factory=list)


def _working_dates(start: date, count: int) -> list[date]:
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _scope(snapshot: ProjectSnapshot) -> list[IssueFacts]:
    return snapshot.sprint_issues or snapshot.issues


def _efficiency(snapshot: ProjectSnapshot, daily_capacity: float) -> float:
    sprint = snapshot.sprint
    if not (sprint and sprint.start) or daily_capacity <= 0:
        return 0.8
    elapsed = working_days(sprint.start, min(snapshot.today, sprint.end or snapshot.today)
                           - timedelta(days=1))  # fmt: skip
    done = sum(i.points for i in snapshot.sprint_issues if i.category == "done")
    if elapsed <= 0 or done <= 0:
        return 0.8
    return min(1.5, max(0.3, done / (elapsed * daily_capacity)))


def _rework_chance(snapshot: ProjectSnapshot) -> float:
    runs = [
        r for r in snapshot.runs if r.branch in (None, snapshot.default_branch) and r.conclusion
    ]
    if not runs:
        return 0.1
    failed = sum(r.conclusion in {"failure", "timed_out"} for r in runs)
    return min(0.6, failed / len(runs))


def _prepare(snapshot: ProjectSnapshot, scenario: Scenario) -> _Inputs:
    today = snapshot.today
    sprint = snapshot.sprint
    sprint_days = (
        working_days(sprint.start, sprint.end) if sprint and sprint.start and sprint.end else 10
    ) or 10
    dates = _working_dates(today, HORIZON_WORKING_DAYS)

    # Capacity per member per working day, zero on leave.
    member_days = []
    for member in snapshot.members:
        per_day = member.capacity_points / sprint_days
        leave = [lv for lv in snapshot.leave if lv.employee_id in member.ids]
        member_days.append(
            [0.0 if any(lv.start <= d <= lv.end for lv in leave) else per_day for d in dates]
        )
    # Team-wide extra leave: remove that many person-days, spread over the first weeks.
    for n in range(scenario.extra_leave_days):
        if member_days:
            member_days[n % len(member_days)][n // max(1, len(member_days))] = 0.0

    full_day = sum(m.capacity_points for m in snapshot.members) / sprint_days
    open_issues = [i for i in _scope(snapshot) if i.is_open and i.key not in scenario.remove_issues]
    pointed = [i.points for i in open_issues if i.points > 0]
    default_points = statistics.median(pointed) if pointed else 2.0
    open_keys = {i.key for i in open_issues}
    work = [
        _Work(i.key, i.points or default_points,
              blocked=not scenario.resolve_blockers and any(b in open_keys for b in i.blocked_by),
              rank=PRIORITY_ORDER.get(i.priority.lower(), 2))
        for i in open_issues
    ]  # fmt: skip
    work.sort(key=lambda w: (w.rank, -w.points))

    # Scope cut: drop the lowest-priority items first until enough points are removed.
    to_cut = scenario.cut_points
    for item in sorted(work, key=lambda w: (-w.rank, w.points)):
        if to_cut <= 0:
            break
        if item.points <= to_cut:
            work.remove(item)
            to_cut -= item.points

    default_target = sprint.end if sprint and sprint.end and sprint.end >= today else None
    return _Inputs(
        today=today, work=work, member_days=member_days,
        efficiency=_efficiency(snapshot, full_day), rework_chance=_rework_chance(snapshot),
        target=scenario.target_date or default_target, added_people=scenario.add_people,
        person_day=(full_day / len(snapshot.members)) if snapshot.members else 1.0, dates=dates,
    )  # fmt: skip


def _trial(inputs: _Inputs, rng: random.Random) -> date | None:
    work = [_Work(w.key, w.points, w.blocked, w.rank) for w in inputs.work]
    if not work:
        return inputs.today
    for index, day in enumerate(inputs.dates):
        capacity = sum(days[index] for days in inputs.member_days)
        ramp = 0.5 if index < RAMP_UP_DAYS else 1.0
        capacity += inputs.added_people * inputs.person_day * ramp
        capacity *= inputs.efficiency * rng.lognormvariate(-(NOISE_SIGMA**2) / 2, NOISE_SIGMA)
        for item in work:
            if item.blocked and rng.random() < DAILY_UNBLOCK_CHANCE:
                item.blocked = False
        for item in work:
            if capacity <= 0:
                break
            if item.blocked or item.points <= 0:
                continue
            spent = min(capacity, item.points)
            item.points -= spent
            capacity -= spent
            if item.points <= 1e-9 and not item.reworked and rng.random() < inputs.rework_chance:
                item.reworked = True
                item.points = max(0.5, REWORK_SHARE * next(
                    w.points for w in inputs.work if w.key == item.key))  # fmt: skip
        work = [w for w in work if w.points > 1e-9]
        if not work:
            return day
    return None


def _percentile(values: list[date], q: float) -> date | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def simulate(
    snapshot: ProjectSnapshot, scenario: Scenario | None = None, *, trials: int = 1000,
    seed: int = 7,
) -> SimulationResult:  # fmt: skip
    scenario = scenario or Scenario()
    inputs = _prepare(snapshot, scenario)
    rng = random.Random(seed)  # noqa: S311 - simulation, not cryptography
    finishes = [_trial(inputs, rng) for _ in range(trials)]
    done = [d for d in finishes if d is not None]

    probability = None
    if inputs.target is not None:
        probability = round(sum(d <= inputs.target for d in done) / trials, 3)
    histogram = sorted(Counter(done).items())
    blocked = [w.key for w in inputs.work if w.blocked]
    daily = sum(days[0] for days in inputs.member_days) if inputs.member_days else 0.0

    drivers = []
    if blocked:
        drivers.append(f"{len(blocked)} open issue(s) are blocked ({', '.join(blocked[:4])})")
    if inputs.efficiency < 0.8:
        drivers.append(f"the team is burning {inputs.efficiency:.0%} of planned capacity")
    if inputs.rework_chance >= 0.25:
        drivers.append(f"{inputs.rework_chance:.0%} of recent main-branch CI runs failed (rework)")
    away = sum(row[:20].count(0.0) for row in inputs.member_days)
    if away:
        drivers.append(f"{away} person-day(s) of leave in the next 4 weeks")

    return SimulationResult(
        scenario=scenario.name,
        target_date=inputs.target,
        probability_by_target=probability,
        p50=_percentile(done, 0.5),
        p80=_percentile(done, 0.8),
        p95=_percentile(done, 0.95),
        finish_rate=round(len(done) / trials, 3),
        remaining_points=round(sum(w.points for w in inputs.work), 1),
        remaining_issues=len(inputs.work),
        efficiency=round(inputs.efficiency, 2),
        daily_capacity=round(daily, 2),
        blocked_issues=blocked,
        rework_chance=round(inputs.rework_chance, 2),
        histogram=histogram,
        trials=trials,
        drivers=drivers,
    )
