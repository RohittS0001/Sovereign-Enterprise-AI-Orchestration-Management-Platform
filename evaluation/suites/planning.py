"""Planning intelligence: is the digital twin calibrated, does the slip model discriminate?

- **Twin calibration** — synthetic projects of varied size, team and blockers. For each,
  the twin forecasts P80 and the chance to finish by the sprint end; one independent
  "reality" trial (different random stream) plays out the sprint. Over many projects about
  80% of realized finishes should land on or before P80, and the stated chances should
  match outcomes (Brier score).
- **Slip-model discrimination** — sprint histories where blocked work drives slips (with
  noise); the model is trained on part and scored on held-out sprints (AUC).

This checks the maths end to end (percentiles, probabilities, training) on known ground
truth; real calibration on your own history comes from the predictive model over time.
"""

import random
from datetime import date, timedelta

from backend.app.domains.analytics import twin
from backend.app.domains.analytics.predictive import FEATURES, train
from backend.app.domains.analytics.snapshot import IssueFacts, Member, ProjectSnapshot, SprintFacts
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset

MONDAY = date(2026, 11, 9)


def _project(rng: random.Random, n: int) -> ProjectSnapshot:
    members = [
        Member(f"m{i}", None, f"e{i}", rng.choice([6, 8, 10, 12])) for i in range(rng.randint(2, 6))
    ]
    capacity = sum(m.capacity_points for m in members)
    load = rng.uniform(0.5, 1.6)  # from comfortable to badly over-committed
    issues, total, k = [], 0.0, 0
    while total < capacity * load:
        points = rng.choice([1, 2, 3, 5, 8])
        issues.append(
            IssueFacts(
                key=f"P{n}-{k}",
                summary="",
                status="todo",
                category="todo",
                priority=rng.choice(["High", "Medium", "Low"]),
                assignee=None,
                assignee_id=None,
                due=None,
                points=points,
                issue_type="Story",
                blocked_by=[f"P{n}-0"] if k and rng.random() < 0.1 else [],
            )
        )
        total += points
        k += 1
    sprint = SprintFacts(n, f"S{n}", "active", MONDAY, MONDAY + timedelta(days=11))
    return ProjectSnapshot(
        key=f"P{n}",
        name=f"P{n}",
        today=MONDAY,
        issues=issues,
        sprint=sprint,
        sprint_issue_keys={i.key for i in issues},
        members=members,
    )


def _auc(scores: list[tuple[float, int]]) -> float:
    positives = [s for s, y in scores if y]
    negatives = [s for s, y in scores if not y]
    if not positives or not negatives:
        return 0.0
    wins = sum((p > q) + 0.5 * (p == q) for p in positives for q in negatives)
    return wins / (len(positives) * len(negatives))


async def run() -> SuiteResult:
    data = load_dataset("planning")
    t, cfg, slip_cfg = data["thresholds"], data["twin"], data["slip_model"]
    result = SuiteResult("planning")

    with Timer() as timer:
        rng = random.Random(cfg["seed"])  # noqa: S311 - simulation
        covered = brier = 0.0
        for n in range(cfg["projects"]):
            snapshot = _project(rng, n)
            forecast = twin.simulate(snapshot, trials=cfg["trials"], seed=n)
            inputs = twin._prepare(snapshot, twin.Scenario())  # noqa: SLF001 - reuse the twin's model
            realized = twin._trial(inputs, random.Random(10_000 + n))  # noqa: S311,SLF001
            realized = realized or MONDAY + timedelta(days=365)
            covered += bool(forecast.p80 and realized <= forecast.p80)
            hit = float(forecast.target_date is not None and realized <= forecast.target_date)
            brier += ((forecast.probability_by_target or 0.0) - hit) ** 2
        coverage, brier = covered / cfg["projects"], brier / cfg["projects"]
        result.cases.append(
            CaseResult(
                "twin-p80-coverage",
                t["p80_coverage_min"] <= coverage <= t["p80_coverage_max"],
                {"coverage": round(coverage, 3)},
            )
        )

        slip_rng = random.Random(slip_cfg["seed"])  # noqa: S311 - simulation
        sprints = []
        for _ in range(slip_cfg["sprints"]):
            blocked = slip_rng.random()
            slipped = int(blocked + slip_rng.gauss(0, 0.2) > 0.55)
            days = [
                (
                    {
                        "blocked_share": min(1.0, max(0.0, blocked + slip_rng.gauss(0, 0.1))),
                        "completion_ratio": max(0.0, 0.9 - blocked / 2 + slip_rng.gauss(0, 0.05)),
                        "elapsed_share": (d + 1) / slip_cfg["days_per_sprint"],
                    },
                    slipped,
                )
                for d in range(slip_cfg["days_per_sprint"])
            ]
            sprints.append(days)
        cut = int(len(sprints) * (1 - slip_cfg["holdout_share"]))
        weights, bias = train([row for s in sprints[:cut] for row in s])
        scored = [
            (bias + sum(w * x.get(name, 0.0) for w, name in zip(weights, FEATURES, strict=True)), y)
            for s in sprints[cut:]
            for x, y in s
        ]
        auc = _auc(scored)
        result.cases.append(
            CaseResult("slip-model-auc", auc >= t["slip_auc"], {"auc": round(auc, 3)})
        )

    result.metrics = [
        Metric(
            "p80_coverage",
            round(coverage, 4),
            t["p80_coverage_min"],
            description=f"Realized finishes ≤ P80 (target band {t['p80_coverage_min']}–{t['p80_coverage_max']})",
        ),
        Metric(
            "p80_coverage_ceiling",
            round(coverage, 4),
            t["p80_coverage_max"],
            higher_is_better=False,
            description="Above this, forecasts are padded",
        ),
        Metric("brier_score", round(brier, 4), t["brier_score"], higher_is_better=False),
        Metric("slip_auc", round(auc, 4), t["slip_auc"]),
    ]
    result.seconds = timer.seconds
    return result
