"""AI chief of staff: a portfolio brief, the decisions it needs, and drafted escalations.

``portfolio_brief`` looks at every active project (health, digital-twin forecast,
negotiation recommendation) and returns what needs a decision this week. ``spoken``
turns it into a ~2-minute script for voice briefings (spoken in the browser).
``propose_escalations`` drafts an escalation e-mail per high-risk project through the
tool gateway as the requesting user's *agent* — so each one is a pending approval, never
sent automatically.
"""

import asyncio
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security.permissions import Principal
from backend.app.db.models import Project
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.contradictions import find_contradictions
from backend.app.domains.analytics.forecast import forecast_sprint
from backend.app.domains.analytics.negotiation import build_options, recommend
from backend.app.domains.analytics.risk import assess_risk
from backend.app.domains.analytics.workload import compute_workload
from backend.app.tools.gateway import ToolGateway

ESCALATE_AT = 60  # risk score


class ProjectBrief(BaseModel):
    key: str
    name: str
    risk_score: int
    risk_level: str
    top_risks: list[str] = Field(default_factory=list)
    sprint_status: str | None = None
    chance_by_target: float | None = None
    target_date: date | None = None
    likely_finish: date | None = None
    recommendation: str | None = None
    decision_needed: str | None = None
    recipients: list[str] = Field(default_factory=list)


class PortfolioBrief(BaseModel):
    as_of: date
    projects: list[ProjectBrief]
    decisions: list[str]
    spoken_text: str


async def _project_brief(
    collector: SnapshotCollector, project: Project, today: date | None
) -> ProjectBrief:
    snapshot = await collector.collect(project, today=today)
    forecast = forecast_sprint(snapshot)
    risk = assess_risk(
        snapshot, forecast, compute_workload(snapshot), find_contradictions(snapshot)
    )
    baseline, options = await asyncio.to_thread(build_options, snapshot, None, trials=300)
    chosen = next((o for o in options if o.id == recommend(options)), None)
    top = sorted((f for f in risk.factors if f.score > 0), key=lambda f: -f.score / f.max_score)[:3]
    at_risk = (baseline.probability_by_target or 1) < 0.8 or risk.score >= ESCALATE_AT
    decision = None
    if at_risk and chosen:
        decision = f"{project.key}: approve '{chosen.title}' or choose another option"
    elif at_risk:
        decision = f"{project.key}: no option reaches 80% confidence; decide on scope or date"
    return ProjectBrief(
        key=project.key, name=project.name, risk_score=risk.score, risk_level=risk.level,
        top_risks=[f"{f.name}: {(f.evidence or [f.recommendation or ''])[0]}" for f in top],
        sprint_status=forecast.status if forecast else None,
        chance_by_target=baseline.probability_by_target, target_date=baseline.target_date,
        likely_finish=baseline.p50,
        recommendation=f"{chosen.title}: {chosen.tradeoff}" if chosen else None,
        decision_needed=decision,
        recipients=list(project.report_recipients or []),
    )  # fmt: skip


def _pct(value: float | None) -> str:
    return "unknown" if value is None else f"{round(value * 100)} percent"


def spoken(briefs: list[ProjectBrief], today: date) -> str:
    """A short script for text-to-speech: worst projects first, plain sentences, no markup."""
    if not briefs:
        return "Good morning. No active projects are configured yet."
    risky = [b for b in briefs if b.decision_needed]
    parts = [f"Good morning. Here is your delivery briefing for {today:%A %d %B}. "
             f"{len(briefs)} active project{'s' if len(briefs) != 1 else ''}, "
             f"{len(risky)} need{'s' if len(risky) == 1 else ''} a decision."]  # fmt: skip
    for b in briefs[:5]:
        line = f"{b.name}: risk {b.risk_score} out of 100, {b.risk_level}."
        if b.target_date:
            line += (
                f" The chance of finishing by {b.target_date:%d %B} is {_pct(b.chance_by_target)}"
            )
            line += f", most likely {b.likely_finish:%d %B}." if b.likely_finish else "."
        if b.top_risks:
            line += f" Main issue: {b.top_risks[0].split(':')[0].lower()}."
        if b.recommendation:
            line += f" Recommended: {b.recommendation.split(':')[0].lower()}."
        parts.append(line)
    parts.append("That's all. Details and the drafted escalations are in the platform.")
    return " ".join(parts)


async def portfolio_brief(
    session: AsyncSession, collector: SnapshotCollector, today: date | None = None
) -> PortfolioBrief:
    projects = list(await session.scalars(select(Project).where(Project.is_active.is_(True))
                                          .order_by(Project.key)))  # fmt: skip
    briefs = [await _project_brief(collector, p, today) for p in projects]
    briefs.sort(key=lambda b: -b.risk_score)
    as_of = today or date.today()
    return PortfolioBrief(as_of=as_of, projects=briefs,
                          decisions=[b.decision_needed for b in briefs if b.decision_needed],
                          spoken_text=spoken(briefs, as_of))  # fmt: skip


async def propose_escalations(
    gateway: ToolGateway, principal: Principal, brief: PortfolioBrief
) -> list[dict]:
    """Draft one escalation e-mail per project that needs a decision (pending approval)."""
    if gateway.registry.get("gmail.create_draft") is None:
        return [{"project": b.key, "status": "skipped", "reason": "Gmail is not connected"}
                for b in brief.projects if b.decision_needed]  # fmt: skip
    results = []
    for b in brief.projects:
        if not b.decision_needed:
            continue
        if not b.recipients:
            results.append(
                {"project": b.key, "status": "skipped", "reason": "no report recipients"}
            )
            continue
        body = "\n".join([
            f"Hello,\n\n{b.name} ({b.key}) needs a decision this week.",
            f"Risk {b.risk_score}/100 ({b.risk_level}); chance of finishing by {b.target_date}: "
            f"{_pct(b.chance_by_target)}.", "", "Main risks:", *[f"- {r}" for r in b.top_risks], "",
            f"Recommendation: {b.recommendation or 'see the options in the platform'}.", "",
            "Please reply with your decision.",
        ])  # fmt: skip
        draft = {"to": b.recipients[:10], "subject": f"[Decision needed] {b.name}", "body": body}
        result = await gateway.invoke(principal.as_agent(), "gmail.create_draft", draft)
        results.append({"project": b.key, "status": result.status.value,
                        "approval_id": result.approval_id, "error": result.error})  # fmt: skip
    return results
