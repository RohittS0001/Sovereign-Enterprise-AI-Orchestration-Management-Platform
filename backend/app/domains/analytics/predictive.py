"""Predictive slip model: learn this organization's early warning signals from its history.

Every health computation stores a daily feature row per project (``ProjectSignal``).
When a sprint has ended, its rows are labelled with what actually happened (did the team
finish at least 90% of the committed points?). A small L2-regularised logistic regression,
trained on those labelled rows, then predicts the chance that the current sprint slips
and shows which signals push it up or down. Pure Python: the data is small (days ×
sprints), so no numerical libraries are needed. With too little history the forecast's
own completion ratio is used instead, and the answer says so.
"""

import math
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domains.analytics.models import ProjectSignal
from backend.app.domains.analytics.results import ProjectHealth

SLIP_BELOW = 0.9  # finishing under 90% of committed points counts as a slip
MIN_ROWS, MIN_SPRINTS = 20, 3

FEATURES = (
    "risk_score",
    "completion_ratio",
    "elapsed_share",
    "blocked_share",
    "overdue_share",
    "overloaded_members",
    "contradictions",
    "failing_builds",
    "production",
    "budget",
)


def features(health: ProjectHealth) -> dict[str, float]:
    """Numbers known on any day of a sprint, scaled roughly to 0..1."""
    f = health.forecast
    factors = {x.name: x.score / x.max_score for x in health.risk.factors if x.max_score}
    total_issues = max(1, sum(health.issue_counts.values()))
    elapsed = (f.working_days_elapsed / f.working_days_total) if f and f.working_days_total else 0.0
    return {
        "risk_score": health.risk.score / 100,
        "completion_ratio": f.completion_ratio if f else 0.0,
        "elapsed_share": elapsed,
        "blocked_share": factors.get("Blocked work", 0.0),
        "overdue_share": factors.get("Overdue work", 0.0),
        "overloaded_members": min(
            1.0, sum(m.status == "overloaded" for m in health.workload.members) / 3
        ),
        "contradictions": min(1.0, len(health.contradictions) / 5),
        "failing_builds": factors.get("Delivery pipeline", 0.0),
        "production": factors.get("Production health", 0.0),
        "budget": factors.get("Budget", 0.0),
    } | {"_open_share": health.issue_counts.get("todo", 0) / total_issues}


async def record_signal(session: AsyncSession, health: ProjectHealth) -> None:
    """Upsert today's row; label the sprint's rows once it has ended."""
    f = health.forecast
    sprint_id = f.sprint if f else None
    row = await session.scalar(
        select(ProjectSignal).where(
            ProjectSignal.project_key == health.project, ProjectSignal.day == health.as_of
        )
    )
    values = {k: round(v, 4) for k, v in features(health).items()}
    if row is None:
        session.add(
            ProjectSignal(
                project_key=health.project, day=health.as_of, sprint=sprint_id, features=values
            )
        )
    else:
        row.features, row.sprint = values, sprint_id
    if f and f.end and health.as_of >= f.end and f.committed_points:
        outcome = f.done_points / f.committed_points
        rows = await session.scalars(
            select(ProjectSignal).where(
                ProjectSignal.project_key == health.project, ProjectSignal.sprint == sprint_id
            )
        )
        for labelled in rows:
            labelled.outcome_ratio = round(outcome, 4)
    await session.commit()


class Driver(BaseModel):
    feature: str
    effect: float  # contribution to the log-odds; positive raises the slip chance


class SlipPrediction(BaseModel):
    project: str
    as_of: date
    slip_probability: float
    method: str  # "model" | "forecast"
    trained_on: int = 0
    sprints: int = 0
    drivers: list[Driver] = Field(default_factory=list)
    note: str | None = None


def train(
    rows: list[tuple[dict[str, float], int]], epochs: int = 400, lr: float = 0.5, l2: float = 0.01
) -> tuple[list[float], float]:
    """Logistic regression by batch gradient descent; returns (weights, bias)."""
    weights, bias = [0.0] * len(FEATURES), 0.0
    n = len(rows)
    for _ in range(epochs):
        grad_w, grad_b = [0.0] * len(FEATURES), 0.0
        for x, y in rows:
            z = bias + sum(w * x.get(name, 0.0) for w, name in zip(weights, FEATURES, strict=True))
            error = 1 / (1 + math.exp(-max(-30.0, min(30.0, z)))) - y
            grad_b += error
            for i, name in enumerate(FEATURES):
                grad_w[i] += error * x.get(name, 0.0)
        bias -= lr * grad_b / n
        weights = [w - lr * (g / n + l2 * w) for w, g in zip(weights, grad_w, strict=True)]
    return weights, bias


async def predict_slip(session: AsyncSession, health: ProjectHealth) -> SlipPrediction:
    rows = list(
        await session.scalars(select(ProjectSignal).where(ProjectSignal.outcome_ratio.is_not(None)))
    )
    labelled = [(r.features, int(r.outcome_ratio < SLIP_BELOW)) for r in rows]
    sprints = len({(r.project_key, r.sprint) for r in rows})
    current = features(health)
    if len(labelled) < MIN_ROWS or sprints < MIN_SPRINTS or len({y for _, y in labelled}) < 2:
        ratio = health.forecast.completion_ratio if health.forecast else 0.5
        return SlipPrediction(
            project=health.project,
            as_of=health.as_of,
            method="forecast",
            slip_probability=round(max(0.0, min(1.0, 1 - ratio)), 3),
            trained_on=len(labelled),
            sprints=sprints,
            note=f"Not enough history yet ({len(labelled)} labelled days over {sprints} sprints; "
            f"need {MIN_ROWS} over {MIN_SPRINTS} with both outcomes). "
            "Using the burn-rate forecast.",
        )
    weights, bias = train(labelled)
    contributions = {
        name: w * current.get(name, 0.0) for w, name in zip(weights, FEATURES, strict=True)
    }
    z = bias + sum(contributions.values())
    drivers = sorted(
        (Driver(feature=k, effect=round(v, 3)) for k, v in contributions.items() if abs(v) > 0.01),
        key=lambda d: -abs(d.effect),
    )[:5]
    return SlipPrediction(
        project=health.project,
        as_of=health.as_of,
        method="model",
        slip_probability=round(1 / (1 + math.exp(-z)), 3),
        trained_on=len(labelled),
        sprints=sprints,
        drivers=drivers,
    )
