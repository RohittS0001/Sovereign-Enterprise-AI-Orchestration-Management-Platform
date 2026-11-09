"""Sprint completion forecast from the burn rate so far."""

from datetime import date, timedelta

from backend.app.analytics.metrics.results import SprintForecast
from backend.app.analytics.snapshot import ProjectSnapshot


def working_days(start: date, end: date) -> int:
    """Weekdays in the inclusive range [start, end]."""
    if end < start:
        return 0
    days = (end - start).days + 1
    full_weeks, extra = divmod(days, 7)
    count = full_weeks * 5
    for offset in range(extra):
        if (start + timedelta(days=full_weeks * 7 + offset)).weekday() < 5:
            count += 1
    return count


def forecast_sprint(snapshot: ProjectSnapshot) -> SprintForecast | None:
    sprint = snapshot.sprint
    if sprint is None:
        return None

    issues = snapshot.sprint_issues
    committed = sum(i.points for i in issues)
    done = sum(i.points for i in issues if i.category == "done")
    remaining = [i.key for i in issues if i.is_open]

    if not (sprint.start and sprint.end) or committed == 0:
        return SprintForecast(
            sprint=sprint.name, start=sprint.start, end=sprint.end, working_days_total=0,
            working_days_elapsed=0, working_days_left=0, committed_points=committed,
            done_points=done, burn_rate_per_day=0, projected_points=done,
            completion_ratio=0, status="unknown", remaining_issues=remaining,
            note="Sprint dates or story points are missing",
        )  # fmt: skip

    today = snapshot.today
    total = working_days(sprint.start, sprint.end)
    elapsed = working_days(sprint.start, min(today, sprint.end) - timedelta(days=1))
    left = max(0, total - elapsed)
    rate = done / elapsed if elapsed else 0.0
    projected = min(committed, done + rate * left) if elapsed else done
    ratio = projected / committed

    if elapsed == 0:
        status, note = "unknown", "Sprint has not started yet"
    elif ratio >= 0.9:
        status, note = "on_track", None
    elif ratio >= 0.7:
        status, note = "at_risk", None
    else:
        status, note = "off_track", None

    return SprintForecast(
        sprint=sprint.name,
        start=sprint.start,
        end=sprint.end,
        working_days_total=total,
        working_days_elapsed=elapsed,
        working_days_left=left,
        committed_points=committed,
        done_points=done,
        burn_rate_per_day=round(rate, 2),
        projected_points=round(projected, 1),
        completion_ratio=round(ratio, 2),
        status=status,
        remaining_issues=remaining,
        note=note,
    )
