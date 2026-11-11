"""Compliance autopilot: map platform evidence to SOC 2 and ISO 27001 controls.

Each control collects evidence from the running platform: the audit chain, users and
roles, access tokens, approval policy, configuration, and every active project's pull
requests, CI runs and incidents. The result is an evidence pack with a status per
control (pass / warn / fail / unknown). It shows how the platform supports an audit; it
does not replace one.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, endpoint_locality
from backend.app.core.security.permissions import Role
from backend.app.db.base import as_utc
from backend.app.db.models import ApiToken, ApprovalRequest, AuditEvent, Project, User
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.governance.audit.service import AuditService

Status = Literal["pass", "warn", "fail", "unknown"]


class ControlResult(BaseModel):
    id: str
    title: str
    soc2: list[str]
    iso27001: list[str]
    status: Status
    evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ComplianceReport(BaseModel):
    generated_at: datetime
    summary: dict[str, int]
    controls: list[ControlResult]

    def markdown(self) -> str:
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "unknown": "❔"}
        lines = [
            f"# Compliance evidence pack — {self.generated_at:%Y-%m-%d %H:%M} UTC",
            "",
            " · ".join(f"{icon[k]} {v} {k}" for k, v in self.summary.items()),
            "",
            "_Evidence collected automatically from the platform; review before submitting._",
            "",
        ]
        for c in self.controls:
            lines += [
                f"## {icon[c.status]} {c.title}",
                "",
                f"SOC 2: {', '.join(c.soc2)} · ISO 27001: {', '.join(c.iso27001)}",
                "",
            ]
            lines += [f"- {e}" for e in c.evidence]
            lines += [f"- **Gap:** {g}" for g in c.gaps]
            lines.append("")
        return "\n".join(lines)


def _control(
    cid: str,
    title: str,
    soc2: list[str],
    iso: list[str],
    evidence: list[str],
    gaps: list[str],
    *,
    fail: bool = False,
    unknown: bool = False,
) -> ControlResult:
    status: Status = "unknown" if unknown else "fail" if fail else "warn" if gaps else "pass"
    return ControlResult(
        id=cid, title=title, soc2=soc2, iso27001=iso, status=status, evidence=evidence, gaps=gaps
    )


async def _access(session: AsyncSession) -> ControlResult:
    roles = Counter(
        r
        for (r,) in (await session.execute(select(User.role).where(User.is_active.is_(True)))).all()
    )
    total, admins = sum(roles.values()), roles.get(Role.ADMIN, 0)
    evidence = [
        "Role-based access control enforced on every API route and agent tool",
        "The AI agent acts with the user's permissions ∩ agent scopes (least privilege)",
        "Active users by role: " + ", ".join(f"{r.value} {n}" for r, n in sorted(roles.items())),
    ]
    gaps = []
    if admins > max(2, round(0.2 * total)):
        gaps.append(f"{admins} of {total} active users are admins; review whether all need it")
    return _control(
        "access-control",
        "Logical access and least privilege",
        ["CC6.1", "CC6.3"],
        ["A.5.15", "A.5.18", "A.8.2"],
        evidence,
        gaps,
    )


async def _authentication(session: AsyncSession, settings: Settings) -> ControlResult:
    now = datetime.now(UTC)
    tokens = list(await session.scalars(select(ApiToken).where(ApiToken.revoked_at.is_(None))))
    active = [t for t in tokens if t.expires_at is None or as_utc(t.expires_at) > now]
    stale = [
        t
        for t in active
        if t.last_used_at is None or as_utc(t.last_used_at) < now - timedelta(days=30)
    ]
    evidence = [
        f"Access tokens expire after {settings.access_token_ttl_minutes} minutes; refresh tokens "
        "rotate on every use and reuse revokes all sessions",
        "Passwords hashed with Argon2; login attempts are rate-limited",
        f"{len(active)} active MCP access tokens (hashed, max {settings.mcp_token_max_days} days)",
    ]
    gaps = []
    if settings.access_token_ttl_minutes > 30:
        gaps.append("Access token lifetime above 30 minutes")
    if stale:
        gaps.append(f"{len(stale)} access token(s) unused for 30+ days; revoke them")
    return _control(
        "authentication",
        "Authentication and credential management",
        ["CC6.1", "CC6.2"],
        ["A.5.17", "A.8.5"],
        evidence,
        gaps,
    )


async def _logging(session: AsyncSession, settings: Settings) -> ControlResult:
    verification = await AuditService(session).verify_chain()
    since = datetime.now(UTC) - timedelta(days=30)
    recent = (
        await session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.occurred_at >= since)
        )
        or 0
    )
    evidence = [
        f"Audit log hash chain verified: {verification.checked} events, "
        f"{'intact' if verification.valid else 'TAMPERING DETECTED'}",
        f"{recent} audit events in the last 30 days (logins, tool calls, approvals, admin changes)",
        "Prometheus metrics "
        + ("enabled" if settings.metrics_enabled else "disabled")
        + "; tracing "
        + ("exported via OTLP" if settings.otel_exporter_otlp_endpoint else "not exported"),
    ]
    gaps = (
        [] if settings.otel_exporter_otlp_endpoint else ["Traces are not exported to a collector"]
    )
    return _control(
        "logging",
        "Event logging and monitoring",
        ["CC7.2", "CC4.1"],
        ["A.8.15", "A.8.16"],
        evidence,
        gaps,
        fail=not verification.valid,
    )


async def _oversight(session: AsyncSession, settings: Settings) -> ControlResult:
    decided = Counter(
        s.value for (s,) in (await session.execute(select(ApprovalRequest.status))).all()
    )
    evidence = [
        "Agent and MCP write actions wait for human approval; the approver needs the action's "
        "own permission",
        f"Approval outcomes so far: {dict(decided) or 'none'}",
    ]
    gaps = []
    if settings.approval_exempt_tools:
        gaps.append(f"Writes exempt from approval: {', '.join(settings.approval_exempt_tools)}")
    if settings.approval_allow_self_approval:
        gaps.append("Self-approval allowed; enable four-eyes (APPROVAL_ALLOW_SELF_APPROVAL=false)")
    return _control(
        "ai-oversight",
        "Human oversight of automated actions",
        ["CC6.8", "CC8.1"],
        ["A.5.8", "A.8.32"],
        evidence,
        gaps,
    )


async def _change(session: AsyncSession, collector: SnapshotCollector) -> ControlResult:
    merged = reviewed = failing = 0
    projects = list(await session.scalars(select(Project).where(Project.is_active.is_(True))))
    for project in projects:
        snapshot = await collector.collect(project)
        for pull in snapshot.pulls:
            if pull.state == "merged":
                merged += 1
                reviewed += "APPROVED" in pull.review_states
        runs = sorted(
            (r for r in snapshot.runs if r.branch == snapshot.default_branch and r.conclusion),
            key=lambda r: r.created or snapshot.today,
            reverse=True,
        )
        failing += bool(runs) and runs[0].conclusion in {"failure", "timed_out"}
    if not merged:
        return _control(
            "change-management",
            "Change management (review and CI)",
            ["CC8.1"],
            ["A.8.32", "A.8.25"],
            ["No merged pull requests in connected repositories"],
            [],
            unknown=True,
        )
    ratio = reviewed / merged
    evidence = [
        f"{reviewed} of {merged} merged pull requests had an approving review ({ratio:.0%})",
        f"{failing} project(s) with a failing latest build on the main branch",
    ]
    gaps = []
    if ratio < 0.8:
        gaps.append(
            "Under 80% of merged changes were reviewed; require reviews via branch protection"
        )
    if failing:
        gaps.append("Main branch build is failing")
    return _control(
        "change-management",
        "Change management (review and CI)",
        ["CC8.1"],
        ["A.8.32", "A.8.25"],
        evidence,
        gaps,
    )


_WHERE = {
    "local": "on this host",
    "self_hosted": "on your own network/cluster",
    "external": "on an external (third-party) endpoint",
}


def _data_protection(settings: Settings) -> ControlResult:
    evidence = [
        "Secrets, card numbers, Aadhaar, PAN, SSN and IBAN are redacted before any model sees data",
        "Tool output is wrapped as untrusted data and scanned for prompt injection",
    ]
    gaps = []
    # Where data goes depends on configuration; never claim more than it says.
    if settings.llm_configured:
        llm = settings.llm_locality
        evidence.append(f"Language models run {_WHERE[llm]} ({settings.llm_provider})")
        if llm == "external":
            gaps.append(
                "LLM_BASE_URL is an external service: redacted prompts and project data leave "
                "your infrastructure; use a self-hosted model to keep them in"
            )
    else:
        evidence.append("No language model configured (agent endpoints disabled)")
    embedding = settings.embedding_locality
    if embedding is None:
        evidence.append("Embeddings are computed in-process (no embedding server)")
    else:
        redacted = " (inputs redacted before sending)" if settings.embedding_redacts else ""
        evidence.append(f"Embeddings run {_WHERE[embedding]}{redacted}")
        if embedding == "external" and not settings.embedding_redacts:
            gaps.append(
                "Document text and queries go unredacted to an external embedding server "
                "(EMBEDDING_REDACTION=never)"
            )
        elif embedding == "external":
            gaps.append(
                "Document text (redacted) and queries leave your infrastructure for an external "
                "embedding server"
            )
    if settings.reranker_url and endpoint_locality(settings.reranker_url) == "external":
        gaps.append("RERANKER_URL is external: queries and passages leave your infrastructure")
    if not settings.gmail_allowed_recipient_domains:
        gaps.append("E-mail drafts may go to any domain; set GMAIL_ALLOWED_RECIPIENT_DOMAINS")
    if not settings.github_allowed_repos and settings.github_configured:
        gaps.append("GitHub access is not limited to an allow-list (GITHUB_ALLOWED_REPOS)")
    return _control(
        "data-protection",
        "Data protection and leakage prevention",
        ["CC6.7", "C1.1"],
        ["A.8.11", "A.8.12", "A.5.34"],
        evidence,
        gaps,
    )


async def _incidents(session: AsyncSession, collector: SnapshotCollector) -> ControlResult:
    stale: list[str] = []
    covered = False
    for project in await session.scalars(select(Project).where(Project.is_active.is_(True))):
        snapshot = await collector.collect(project)
        if snapshot.ops is None:
            continue
        covered = True
        stale += [
            f"{project.key}: {i.source} {i.id} ({i.severity}) open since {i.created}"
            for i in snapshot.ops.incidents
            if i.created
            and (snapshot.today - i.created).days > 2
            and (i.severity or "").lower() in {"high", "critical", "p1", "p2"}
        ]
    if not covered:
        return _control(
            "incident-management",
            "Incident management",
            ["CC7.3", "CC7.4"],
            ["A.5.24", "A.5.26"],
            ["No incident tool linked to any project"],
            [],
            unknown=True,
        )
    evidence = ["Production incidents are tracked from the connected incident tools per project"]
    gaps = [f"High-severity incident open more than 2 days: {s}" for s in stale]
    return _control(
        "incident-management",
        "Incident management",
        ["CC7.3", "CC7.4"],
        ["A.5.24", "A.5.26"],
        evidence,
        gaps,
    )


def _configuration(settings: Settings, integrations: dict[str, bool]) -> ControlResult:
    s = settings
    connected = ", ".join(n for n, on in integrations.items() if on) or "none"
    evidence = [
        f"Environment: {s.environment}",
        f"Connected integrations: {connected}",
        f"Rate limits: login {s.login_rate_limit_attempts}/{s.login_rate_limit_window_seconds}s, "
        f"agent {s.agent_rate_limit_per_minute}/min, MCP {s.mcp_rate_limit_per_minute}/min",
    ]
    gaps, fail = [], False
    if "*" in settings.cors_origins:
        gaps.append("CORS allows any origin")
    if settings.is_production:
        if settings.demo_mode:
            gaps.append("Demo mode is on in production")
            fail = True
        if settings.metrics_enabled and not settings.metrics_token.get_secret_value():
            gaps.append("/metrics is not protected by METRICS_TOKEN")
    return _control(
        "secure-configuration",
        "Secure configuration and suppliers",
        ["CC6.6", "CC9.2"],
        ["A.8.9", "A.5.19", "A.5.21"],
        evidence,
        gaps,
        fail=fail,
    )


async def build_report(
    session: AsyncSession,
    settings: Settings,
    collector: SnapshotCollector,
    integrations: dict[str, bool],
) -> ComplianceReport:
    controls = [
        await _access(session),
        await _authentication(session, settings),
        await _logging(session, settings),
        await _oversight(session, settings),
        await _change(session, collector),
        _data_protection(settings),
        await _incidents(session, collector),
        _configuration(settings, integrations),
    ]
    summary = Counter(c.status for c in controls)
    return ComplianceReport(
        generated_at=datetime.now(UTC),
        summary={k: summary.get(k, 0) for k in ("pass", "warn", "fail", "unknown")},
        controls=controls,
    )
