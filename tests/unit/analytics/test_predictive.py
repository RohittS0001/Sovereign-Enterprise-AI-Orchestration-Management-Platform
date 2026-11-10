"""Predictive slip model: training, cold start and labelling."""

import random
from datetime import date, timedelta

from sqlalchemy import select

from backend.app.db.models import ProjectSignal
from backend.app.domains.analytics.predictive import FEATURES, predict_slip, record_signal, train
from backend.app.domains.analytics.results import (
    ProjectHealth,
    RiskAssessment,
    RiskFactor,
    SprintForecast,
    Workload,
)

DAY = date(2026, 11, 9)


def health(
    *,
    blocked: float,
    ratio: float,
    as_of: date = DAY,
    sprint: str = "S9",
    end: date | None = None,
    done: float = 5,
    committed: float = 10,
) -> ProjectHealth:
    forecast = SprintForecast(
        sprint=sprint,
        start=as_of - timedelta(days=5),
        end=end or as_of + timedelta(days=5),
        working_days_total=10,
        working_days_elapsed=5,
        working_days_left=5,
        committed_points=committed,
        done_points=done,
        burn_rate_per_day=1,
        projected_points=done,
        completion_ratio=ratio,
        status="at_risk",
        remaining_issues=[],
    )
    factors = [RiskFactor(name="Blocked work", score=15 * blocked, max_score=15, evidence=[])]
    return ProjectHealth(
        project="PHX",
        name="P",
        as_of=as_of,
        risk=RiskAssessment(score=int(60 * blocked), level="medium", factors=factors),
        forecast=forecast,
        workload=Workload(period_end=as_of, members=[], unassigned_open=[], suggestions=[]),
        contradictions=[],
        issue_counts={"todo": 2},
        warnings=[],
    )


def test_training_learns_that_blocked_work_predicts_slips() -> None:
    rng = random.Random(1)  # noqa: S311 - test data
    rows = []
    for _ in range(200):
        blocked = rng.random()
        rows.append(({"blocked_share": blocked, "completion_ratio": 0.7}, int(blocked > 0.5)))
    weights, _ = train(rows)
    assert weights[FEATURES.index("blocked_share")] > 1.0


async def test_cold_start_uses_the_forecast(database) -> None:
    async with database.session() as session:
        prediction = await predict_slip(session, health(blocked=0.8, ratio=0.6))
    assert prediction.method == "forecast" and prediction.slip_probability == 0.4
    assert "Not enough history" in prediction.note


async def test_signals_are_recorded_daily_and_labelled_at_sprint_end(database) -> None:
    async with database.session() as session:
        await record_signal(session, health(blocked=0.2, ratio=0.9))
        await record_signal(session, health(blocked=0.3, ratio=0.85))  # same day: upsert
        end = DAY + timedelta(days=4)
        await record_signal(
            session, health(blocked=0.3, ratio=0.7, as_of=end, end=end, done=6, committed=10)
        )
        rows = list(await session.scalars(select(ProjectSignal).order_by(ProjectSignal.day)))

    assert len(rows) == 2 and rows[0].features["blocked_share"] == 0.3
    assert {r.outcome_ratio for r in rows} == {0.6}  # both days labelled with the final ratio


async def test_model_prediction_with_history(database) -> None:
    rng = random.Random(7)  # noqa: S311 - test data
    async with database.session() as session:
        for sprint in range(6):
            for day in range(5):
                blocked = rng.random()
                session.add(
                    ProjectSignal(
                        project_key="PHX",
                        day=DAY - timedelta(days=sprint * 14 + day),
                        sprint=f"S{sprint}",
                        features={"blocked_share": blocked, "completion_ratio": 0.8 - blocked / 2},
                        outcome_ratio=0.6 if blocked > 0.5 else 0.95,
                    )
                )
        await session.commit()
        risky = await predict_slip(session, health(blocked=0.9, ratio=0.5))
        calm = await predict_slip(session, health(blocked=0.05, ratio=0.95))

    assert risky.method == "model" and risky.trained_on == 30 and risky.sprints == 6
    assert risky.slip_probability > 0.7 > 0.3 > calm.slip_probability
    assert risky.drivers[0].feature in {"blocked_share", "completion_ratio"}
