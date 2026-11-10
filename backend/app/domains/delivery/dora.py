"""DORA metrics: deployment frequency, lead time for changes, change failure rate and
time to restore service, each banded Elite/High/Medium/Low (DORA State of DevOps bands).

Sources, best first:
- **deployments** — ops connectors (Argo CD, Jenkins, ...) scoped to the project. Names
  containing staging/dev/test/qa/preview count as non-production and are excluded.
- **lead time** — pull requests: opened → merged (a proxy for commit → production).
- **restore time** — incidents with both a created and a resolved date.

Missing sources give ``None`` with a reason, never a made-up number. Dates are days, so
"0 days" means the same day.
"""

import re
import statistics
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.domains.analytics.snapshot import ProjectSnapshot

Band = Literal["elite", "high", "medium", "low"]
BAND_ORDER: list[Band] = ["elite", "high", "medium", "low"]
NON_PROD = re.compile(r"\b(staging|stage|stg|dev|develop|test|qa|uat|preview|sandbox)\b", re.I)
FAILED = {"failure", "failed", "error", "degraded", "out_of_sync", "aborted"}
SUCCEEDED = {"success", "succeeded", "healthy", "synced", "deployed"}


class DoraMetric(BaseModel):
    name: str
    value: float | None
    unit: str
    band: Band | None
    evidence: list[str] = Field(default_factory=list)
    source: str | None = None
    note: str | None = None


class DoraReport(BaseModel):
    project: str
    as_of: date
    window_days: int
    deployment_frequency: DoraMetric
    lead_time: DoraMetric
    change_failure_rate: DoraMetric
    time_to_restore: DoraMetric
    overall: Band | None = Field(description="Median band of the metrics that have data")
    ci_failure_rate: float | None = Field(
        default=None, description="Leading signal: failing runs on the default branch"
    )
    recommendations: list[str] = Field(default_factory=list)


def _band(value: float, limits: tuple[float, float, float], higher_is_better: bool) -> Band:
    for band, limit in zip(BAND_ORDER, limits, strict=False):
        if (value >= limit) if higher_is_better else (value <= limit):
            return band
    return "low"


def _production(deployments: list[Any]) -> list[Any]:
    return [d for d in deployments if not NON_PROD.search(f"{d.name} {d.detail or ''}")]


def dora_metrics(
    snapshot: ProjectSnapshot, incidents: list[Any] | None = None, *, window_days: int = 30
) -> DoraReport:
    today = snapshot.today
    since = today - timedelta(days=window_days)
    weeks = window_days / 7

    # ---------------------------------------------------------------- deployments
    deployments = [
        d
        for d in (snapshot.ops.deployments if snapshot.ops else [])
        if d.finished and since <= d.finished <= today
    ]
    prod = _production(deployments)
    prod_ok = [d for d in prod if d.status.lower() in SUCCEEDED]
    prod_failed = [d for d in prod if d.status.lower() in FAILED]
    if snapshot.ops is None:
        frequency = DoraMetric(
            name="Deployment frequency",
            value=None,
            unit="per week",
            band=None,
            note="No deployment source covers this project",
        )
        failure = DoraMetric(
            name="Change failure rate",
            value=None,
            unit="%",
            band=None,
            note="No deployment source covers this project",
        )
    else:
        per_week = round(len(prod_ok) / weeks, 2)
        # elite: on demand (≥ daily), high: ≥ weekly, medium: ≥ monthly, low: less
        frequency = DoraMetric(
            name="Deployment frequency",
            value=per_week,
            unit="per week",
            band=_band(per_week, (7, 1, 7 / 30), higher_is_better=True),
            evidence=[f"{d.name}: {d.status} on {d.finished}" for d in prod_ok][:10],
            source=", ".join(sorted({d.source for d in prod})) or None,
            note=None if prod else "No production deployments in the window",
        )
        attempted = len(prod_ok) + len(prod_failed)
        if attempted:
            rate = round(100 * len(prod_failed) / attempted, 1)
            failure = DoraMetric(
                name="Change failure rate",
                value=rate,
                unit="%",
                band=_band(rate, (5, 10, 15), higher_is_better=False),
                evidence=[
                    f"{d.name}: {d.status} on {d.finished} ({d.detail or d.source})"
                    for d in prod_failed
                ][:10]
                or [f"{attempted} production deployment(s), none failed"],
                source=frequency.source,
            )
        else:
            failure = DoraMetric(
                name="Change failure rate",
                value=None,
                unit="%",
                band=None,
                note="No finished production deployments in the window",
            )
        skipped = len(deployments) - len(prod)
        if skipped:
            frequency.note = f"{skipped} non-production deployment(s) excluded"

    # ---------------------------------------------------------------- lead time
    merged = [p for p in snapshot.pulls if p.merged and p.created and since <= p.merged <= today]
    if merged:
        days = [(p.merged - p.created).days for p in merged]
        median = float(statistics.median(days))
        lead = DoraMetric(
            name="Lead time for changes",
            value=median,
            unit="days",
            band=_band(median, (0.99, 7, 30), higher_is_better=False),
            evidence=[
                f"{p.repository}#{p.number} {(p.merged - p.created).days} day(s): {p.title}"
                for p in sorted(merged, key=lambda p: p.merged, reverse=True)
            ][:10],
            source="pull requests (opened → merged)",
        )
    else:
        lead = DoraMetric(
            name="Lead time for changes",
            value=None,
            unit="days",
            band=None,
            note="No pull requests merged in the window",
        )

    # ---------------------------------------------------------------- restore time
    restored = [
        i
        for i in incidents or []
        if getattr(i, "resolved", None) and i.created and since <= i.resolved <= today
    ]
    if restored:
        days = float(statistics.median((i.resolved - i.created).days for i in restored))
        restore = DoraMetric(
            name="Time to restore service",
            value=days,
            unit="days",
            band=_band(days, (0, 1, 7), higher_is_better=False),
            evidence=[
                f"{i.source} {i.id} restored in {(i.resolved - i.created).days} day(s): {i.title}"
                for i in restored
            ][:10],
            source=", ".join(sorted({i.source for i in restored})),
        )
    else:
        open_now = [
            i for i in incidents or [] if (i.status or "").lower() not in {"resolved", "closed"}
        ]
        restore = DoraMetric(
            name="Time to restore service",
            value=None,
            unit="days",
            band=None,
            note="No incidents resolved in the window"
            + (f"; {len(open_now)} still open" if open_now else ""),
        )

    # ---------------------------------------------------------------- summary
    metrics = [frequency, lead, failure, restore]
    bands = sorted((BAND_ORDER.index(m.band) for m in metrics if m.band), reverse=False)
    overall = BAND_ORDER[bands[(len(bands) - 1) // 2]] if bands else None

    runs = [
        r
        for r in snapshot.runs
        if r.branch in (None, snapshot.default_branch) and r.conclusion in {"success", "failure"}
    ]
    ci_rate = round(sum(r.conclusion == "failure" for r in runs) / len(runs), 2) if runs else None

    advice = []
    if frequency.band in {"medium", "low"}:
        advice.append("Ship smaller batches more often: automate the release and deploy on merge.")
    if lead.band in {"medium", "low"}:
        advice.append("Cut lead time: smaller pull requests and a one-day review agreement.")
    if failure.band in {"medium", "low"}:
        advice.append(
            "Lower change failures: add a pre-production smoke test and progressive "
            "rollout before production."
        )
    if restore.band in {"medium", "low"}:
        advice.append("Restore faster: rehearse rollback and keep the runbook next to the alert.")
    if ci_rate is not None and ci_rate >= 0.3:
        advice.append(
            f"The default branch fails {round(ci_rate * 100)}% of CI runs; fix the build "
            "before it turns into failed deployments."
        )

    return DoraReport(
        project=snapshot.key,
        as_of=today,
        window_days=window_days,
        deployment_frequency=frequency,
        lead_time=lead,
        change_failure_rate=failure,
        time_to_restore=restore,
        overall=overall,
        ci_failure_rate=ci_rate,
        recommendations=advice,
    )


async def incident_history(hub: Any, snapshot: ProjectSnapshot, limit: int = 50) -> list[Any]:
    """All incidents (open and resolved) for the project's linked ops records."""
    from backend.app.integrations.connectors.ops import PROJECT_LINKS, capabilities

    found: list[Any] = []
    for source, service in hub.by_category("ops").items():
        scope = snapshot.links.get(PROJECT_LINKS.get(source, ""), [])
        if not scope or "list_incidents" not in capabilities(service):
            continue
        try:
            found += await service.list_incidents(open_only=False, scope=scope, limit=limit)
        except Exception as exc:  # noqa: BLE001 - a failing source is a warning, not an error
            snapshot.warnings.append(f"{source} incident history unavailable: {exc}")
    return found
