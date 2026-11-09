"""Evaluation primitives: metrics with thresholds, per-case results, reports."""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

DATASETS = Path(__file__).parent / "datasets"


def load_dataset(name: str) -> dict[str, Any]:
    with open(DATASETS / f"{name}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class Metric:
    name: str
    value: float
    threshold: float | None = None
    higher_is_better: bool = True
    description: str = ""

    @property
    def passed(self) -> bool:
        if self.threshold is None:
            return True
        return (
            self.value >= self.threshold if self.higher_is_better else self.value <= self.threshold
        )


@dataclass
class CaseResult:
    id: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SuiteResult:
    suite: str
    metrics: list[Metric] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)
    skipped: str | None = None
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.skipped is not None or all(m.passed for m in self.metrics)

    def metric(self, name: str) -> Metric:
        return next(m for m in self.metrics if m.name == name)


class Timer:
    def __enter__(self) -> "Timer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.seconds = round(time.perf_counter() - self.start, 2)


def ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


# ------------------------------------------------------------------ reports


def to_markdown(results: list[SuiteResult]) -> str:
    lines = ["# Evaluation report", "", f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC_", ""]
    lines += ["| Suite | Result | Metrics |", "|---|---|---|"]
    for r in results:
        status = f"skipped ({r.skipped})" if r.skipped else ("✅ pass" if r.passed else "❌ FAIL")
        metrics = ", ".join(f"{m.name}={m.value:g}" for m in r.metrics)
        lines.append(f"| {r.suite} | {status} | {metrics} |")
    for r in results:
        if r.skipped:
            continue
        lines += [
            "",
            f"## {r.suite} ({r.seconds}s)",
            "",
            "| Metric | Value | Threshold | |",
            "|---|---|---|---|",
        ]
        for m in r.metrics:
            op = "≥" if m.higher_is_better else "≤"
            threshold = f"{op} {m.threshold:g}" if m.threshold is not None else "—"
            lines.append(f"| {m.name} | {m.value:g} | {threshold} | {'✅' if m.passed else '❌'} |")
        failed = [c for c in r.cases if not c.passed]
        if failed:
            lines += ["", "Failed cases:", ""]
            lines += [f"- `{c.id}` — {json.dumps(c.details, default=str)[:300]}" for c in failed]
    return "\n".join(lines) + "\n"


def write_reports(results: list[SuiteResult], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = directory / f"eval-{stamp}.json"
    md_path = directory / f"eval-{stamp}.md"
    payload = [
        {
            **asdict(r),
            "passed": r.passed,
            "metrics": [{**asdict(m), "passed": m.passed} for m in r.metrics],
        }
        for r in results
    ]
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(results), encoding="utf-8")
    return json_path, md_path
