"""Index every connected platform into the knowledge base, per project, for hybrid search.

Each adapter turns one system's records into ``IndexItem``s (a title, a self-contained text
with the identifiers people search for, the project, when it happened and a link):

=================  ===========================================================  ================
source             what becomes a document                                      who may read it
=================  ===========================================================  ================
jira               each issue + its status history and comments                 projects:read
github             each pull request; CI results per repository                 projects:read
<tracker>          Linear / GitLab / Azure DevOps … items about the project     projects:read
slack, teams_chat  each project channel, one document per channel per day       projects:read
email              project e-mails (subject, sender, body — redacted)           email:read
pagerduty, …       incidents, error groups, deployments, quality gates          projects:read
meetings           transcripts that mention the project                         projects:read
keka, bamboohr …   leave of project members                                     people:read
finance, crm       budget lines and customer deals tied to the project          analytics:read
=================  ===========================================================  ================

"Who may read it" becomes the document's role list (``Visibility.ROLES``; admins always), so
the same SQL access filter that runs before ranking keeps e-mail, HR and finance records away
from roles without that permission. Re-indexing is incremental: unchanged records are
skipped (checksum), changed ones re-embedded, vanished ones removed — only for sources that
were read successfully, so an outage never wipes the index.
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.errors import ConflictError
from backend.app.core.logging import get_logger
from backend.app.core.security.permissions import ROLE_PERMISSIONS, Permission, Principal, Role
from backend.app.db.models import Document, Project, Visibility
from backend.app.domains.analytics.snapshot import ProjectSnapshot
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.governance.guardrails.redaction import redact_text
from backend.app.domains.knowledge.embeddings import Embedder
from backend.app.domains.knowledge.service import KnowledgeService

logger = get_logger(__name__)

MAX_ITEMS_PER_SOURCE = 300
CHAT_DAYS = 30
MEETING_DAYS = 30


@dataclass
class IndexItem:
    source: str
    id: str
    title: str
    text: str
    permission: Permission = Permission.PROJECTS_READ
    occurred_at: datetime | None = None
    url: str | None = None


@dataclass
class SourceResult:
    source: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    error: str | None = None


@dataclass
class IndexResult:
    project: str
    sources: list[SourceResult] = field(default_factory=list)


def roles_with(permission: Permission) -> list[str]:
    """Roles (besides admin, who always reads) holding ``permission``."""
    return sorted(
        r.value for r, perms in ROLE_PERMISSIONS.items() if permission in perms and r != Role.ADMIN
    )


def _at(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.combine(value, time(12), UTC)


def _mentions(text: str, project: Project) -> bool:
    lowered = text.lower()
    return project.key.lower() in lowered or project.name.lower() in lowered


def _lines(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------ adapters


def jira_items(snapshot: ProjectSnapshot, facts: Any) -> list[IndexItem]:
    comments: dict[str, list[Any]] = defaultdict(list)
    for note in facts.notes:
        if note.source == "jira comment":
            comments[note.ref].append(note)
    items = []
    for issue in snapshot.issues:
        history = facts.history.get(issue.key, [])
        meta = (
            f"Project: {snapshot.key} · Type: {issue.issue_type} · Status: {issue.status} · "
            f"Priority: {issue.priority} · Assignee: {issue.assignee or 'unassigned'} · "
            f"Points: {issue.points:g} · Due: {issue.due or '—'}"
        )
        text = _lines(
            f"{issue.key} · {issue.summary}",
            meta,
            f"Blocked by: {', '.join(issue.blocked_by)}" if issue.blocked_by else None,
            "Status history:\n"
            + "\n".join(
                f"- {c.at} {c.author or 'someone'}: {c.from_status} → {c.to_status}"
                for c in history
            )
            if history
            else None,
            "Comments:\n"
            + "\n".join(f"- {n.at} {n.author or 'someone'}: {n.text}" for n in comments[issue.key])
            if comments[issue.key]
            else None,
        )
        items.append(
            IndexItem(
                "jira",
                issue.key,
                f"{issue.key}: {issue.summary}",
                text,
                occurred_at=_at(issue.updated or issue.created),
            )
        )
    return items


def github_items(snapshot: ProjectSnapshot) -> list[IndexItem]:
    items = []
    for pull in snapshot.pulls:
        reviews = ", ".join(pull.review_states) or "no reviews"
        text = _lines(
            f"{pull.repository}#{pull.number} · {pull.title}",
            f"Pull request · State: {pull.state}{' (draft)' if pull.draft else ''} · "
            f"Author: {pull.author or '—'} · Opened: {pull.created or '—'} · "
            f"Merged: {pull.merged or 'not merged'} · Reviews: {reviews}",
            f"Linked issues: {', '.join(pull.issue_keys)}" if pull.issue_keys else None,
            f"Waiting for review from: {', '.join(pull.requested_reviewers)}"
            if pull.requested_reviewers
            else None,
        )
        items.append(
            IndexItem(
                "github",
                f"{pull.repository}#{pull.number}",
                f"PR {pull.repository}#{pull.number}: {pull.title}",
                text,
                occurred_at=_at(pull.merged or pull.created),
                url=pull.url,
            )
        )
    by_repo: dict[str, list[Any]] = defaultdict(list)
    for run in snapshot.runs:
        by_repo[run.repository].append(run)
    for repository, runs in by_repo.items():
        runs = sorted(runs, key=lambda r: r.created or date.min, reverse=True)
        failing = sum(r.conclusion == "failure" for r in runs)
        text = _lines(
            f"CI builds for {repository}: {failing} of {len(runs)} recent runs failed.",
            "\n".join(
                f"- {r.created} {r.branch or '—'}: {r.conclusion or r.status}" for r in runs[:20]
            ),
        )
        items.append(
            IndexItem(
                "github",
                f"ci:{repository}",
                f"CI builds: {repository}",
                text,
                occurred_at=_at(runs[0].created if runs else None),
            )
        )
    return items


def email_items(facts: Any) -> list[IndexItem]:
    return [
        IndexItem(
            "email",
            f"{n.ref}|{n.at}|{n.author}",
            n.ref,
            _lines(f"Subject: {n.ref}", f"From: {n.author} · Date: {n.at}", "", n.text),
            permission=Permission.EMAIL_READ,
            occurred_at=_at(n.at),
        )
        for n in facts.notes
        if n.source == "email"
    ]


async def chat_items(hub: Any, snapshot: ProjectSnapshot) -> dict[str, list[IndexItem]]:
    """One document per channel per day, so a day's discussion stays together."""
    found: dict[str, list[IndexItem]] = {}
    since = snapshot.today - timedelta(days=CHAT_DAYS)
    for source, service in hub.by_category("chat").items():
        channels = snapshot.links.get(
            "slack_channels" if source == "slack" else "teams_channels", []
        )
        if not channels or not callable(getattr(service, "list_messages", None)):
            continue
        items = []
        for channel in channels:
            days: dict[date, list[Any]] = defaultdict(list)
            for message in await service.list_messages(channel, since):
                days[message.sent_at.date()].append(message)
            for day, messages in sorted(days.items()):
                messages.sort(key=lambda m: m.sent_at)
                text = "\n".join(
                    f"{m.sent_at:%H:%M} {m.author or 'someone'}: {redact_text(m.text).text}"
                    for m in messages
                )
                items.append(
                    IndexItem(
                        source,
                        f"{channel}:{day}",
                        f"{source} {channel} · {day}",
                        _lines(f"Channel {channel} on {day} ({snapshot.key})", text),
                        occurred_at=_at(day),
                    )
                )
        found[source] = items
    return found


async def ops_items(hub: Any, snapshot: ProjectSnapshot) -> list[IndexItem]:
    from backend.app.domains.delivery.dora import incident_history

    items = []
    for incident in await incident_history(hub, snapshot):
        text = _lines(
            f"Incident {incident.source} {incident.id} · {incident.title}",
            f"Status: {incident.status} · Severity: {incident.severity or '—'} · "
            f"Service: {incident.service or '—'} · Opened: {incident.created} · "
            f"Resolved: {incident.resolved or 'not yet'}",
        )
        items.append(
            IndexItem(
                "incidents",
                f"{incident.source}:{incident.id}",
                f"Incident {incident.id}: {incident.title}",
                text,
                occurred_at=_at(incident.created),
                url=incident.url,
            )
        )
    if snapshot.ops is not None:
        for error in snapshot.ops.errors:
            items.append(
                IndexItem(
                    "incidents",
                    f"error:{error.source}:{error.id}",
                    f"Error {error.id}: {error.title}",
                    f"Error group {error.id} ({error.source}) · {error.title} · "
                    f"{error.events} events, {error.users} users · last seen "
                    f"{error.last_seen}",
                    occurred_at=_at(error.last_seen),
                    url=getattr(error, "url", None),
                )
            )
        for deployment in snapshot.ops.deployments:
            items.append(
                IndexItem(
                    "incidents",
                    f"deploy:{deployment.source}:{deployment.name}:{deployment.finished}",
                    f"Deployment {deployment.name}: {deployment.status}",
                    f"Deployment {deployment.name} via {deployment.source} · status "
                    f"{deployment.status} · finished {deployment.finished} · "
                    f"{deployment.detail or ''}",
                    occurred_at=_at(deployment.finished),
                )
            )
        for gate in snapshot.ops.quality_gates:
            items.append(
                IndexItem(
                    "incidents",
                    f"gate:{gate.source}:{gate.project}",
                    f"Quality gate {gate.project}: {gate.status}",
                    f"Quality gate {gate.project} ({gate.source}) is {gate.status}. "
                    f"Failing: {'; '.join(gate.failing) or 'nothing'}",
                )
            )
    return items


async def tracker_items(hub: Any, project: Project) -> list[IndexItem]:
    items = []
    for source, service in hub.by_category("tracker").items():
        for item in await service.search_work_items(limit=100):
            if not (item.project == project.key or _mentions(f"{item.key} {item.title}", project)):
                continue
            text = _lines(
                f"{item.key} · {item.title}",
                f"{source} work item · Status: {item.status} · Assignee: {item.assignee or '—'} "
                f"· Priority: {item.priority or '—'} · Due: {item.due or '—'} · Project: "
                f"{item.project or '—'} (related to {project.key})",
            )
            items.append(
                IndexItem(
                    source,
                    item.key,
                    f"{item.key}: {item.title}",
                    text,
                    occurred_at=_at(item.updated),
                    url=item.url,
                )
            )
    return items


async def meeting_items(hub: Any, project: Project, today: date) -> list[IndexItem]:
    items = []
    for source, service in hub.by_category("meetings").items():
        for transcript in await service.list_transcripts(
            today - timedelta(days=MEETING_DAYS), today
        ):
            if not _mentions(f"{transcript.title}\n{transcript.text}", project):
                continue
            items.append(
                IndexItem(
                    "meetings",
                    f"{source}:{transcript.meeting_id}",
                    f"Meeting: {transcript.title}",
                    _lines(
                        f"Meeting transcript · {transcript.title} · {transcript.held_on}",
                        redact_text(transcript.text).text,
                    ),
                    occurred_at=_at(transcript.held_on),
                )
            )
    return items


def people_items(snapshot: ProjectSnapshot) -> list[IndexItem]:
    names = {i.lower(): m.name for m in snapshot.members for i in m.ids}
    items = []
    for leave in snapshot.leave:
        name = names.get(leave.employee_id.lower(), leave.employee_id)
        items.append(
            IndexItem(
                leave.source,
                f"{leave.employee_id}:{leave.start}",
                f"Leave: {name} {leave.start} to {leave.end}",
                f"{name} ({snapshot.key} team) is on leave from {leave.start} to "
                f"{leave.end} (source: {leave.source}).",
                permission=Permission.PEOPLE_READ,
                occurred_at=_at(leave.start),
            )
        )
    return items


def business_items(snapshot: ProjectSnapshot) -> list[IndexItem]:
    items = []
    for line in snapshot.finance or []:
        items.append(
            IndexItem(
                "finance",
                f"{line.source}:{line.project}",
                f"Budget: {line.project}",
                f"Budget vs actual for {line.project} ({snapshot.key}): budget "
                f"{line.budget} {line.currency or ''}, actual {line.actual} "
                f"{line.currency or ''} (source: {line.source}).",
                permission=Permission.ANALYTICS_READ,
            )
        )
    for deal in snapshot.deals or []:
        items.append(
            IndexItem(
                "crm",
                f"{deal.source}:{deal.id}",
                f"Deal: {deal.name}",
                f"Customer deal {deal.name} ({deal.source}) depends on {snapshot.key}: "
                f"stage {deal.stage}, amount {deal.amount}, expected close "
                f"{deal.close_date}.",
                permission=Permission.ANALYTICS_READ,
                occurred_at=_at(deal.close_date),
                url=deal.url,
            )
        )
    return items


# ------------------------------------------------------------------ collection + sync


async def collect_items(
    hub: Any, project: Project, snapshot: ProjectSnapshot
) -> tuple[dict[str, list[IndexItem]], dict[str, str]]:
    """Items per source, plus the sources that could not be read (left untouched)."""
    from backend.app.domains.delivery.delay import collect_delay_facts

    items: dict[str, list[IndexItem]] = {}
    errors: dict[str, str] = {}

    async def guarded(name: str, factory) -> None:
        try:
            result = await factory()
        except Exception as exc:  # noqa: BLE001 - one source failing must not stop the rest
            errors[name] = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            return
        if isinstance(result, dict):
            items.update(result)
        else:
            for item in result:
                items.setdefault(item.source, []).append(item)

    facts = await collect_delay_facts(hub, snapshot, include_email=True)
    if hub.jira is not None:
        items["jira"] = jira_items(snapshot, facts)
    items["email"] = email_items(facts) if getattr(hub, "email", None) is not None else []
    if hub.github is not None:
        items["github"] = github_items(snapshot)
    await guarded("chat", lambda: chat_items(hub, snapshot))
    await guarded("incidents", lambda: ops_items(hub, snapshot))
    await guarded("trackers", lambda: tracker_items(hub, project))
    await guarded("meetings", lambda: meeting_items(hub, project, snapshot.today))
    for item in people_items(snapshot) + business_items(snapshot):
        items.setdefault(item.source, []).append(item)
    for warning in snapshot.warnings + facts.warnings:
        errors.setdefault("warnings", warning)
    return {s: v[:MAX_ITEMS_PER_SOURCE] for s, v in items.items()}, errors


async def index_project(
    session: AsyncSession,
    settings: Settings,
    embedder: Embedder | None,
    principal: Principal,
    hub: Any,
    project: Project,
    snapshot: ProjectSnapshot,
) -> IndexResult:
    """Bring the knowledge base in line with what the project's systems hold now."""
    knowledge = KnowledgeService(session, settings, embedder)
    items_by_source, errors = await collect_items(hub, project, snapshot)
    result = IndexResult(project.key)

    existing: dict[tuple[str, str], Document] = {
        (doc.source_type, (doc.extra or {}).get("source_id")): doc
        for doc in await session.scalars(
            select(Document).where(Document.project_key == project.key)
        )
    }
    for source, items in sorted(items_by_source.items()):
        outcome = SourceResult(source)
        seen: set[str] = set()
        for item in items:
            text = item.text.strip()
            if not text:
                continue
            seen.add(item.id)
            current = existing.get((source, item.id))
            if current is not None:
                if current.checksum == hashlib.sha256(text.encode("utf-8")).hexdigest():
                    outcome.unchanged += 1
                    continue
                await session.delete(current)
                await session.flush()
            try:
                await knowledge.ingest(
                    principal,
                    title=item.title[:300],
                    text=text,
                    source_type=source,
                    visibility=Visibility.ROLES,
                    allowed_roles=roles_with(item.permission),
                    tags=[source, project.key],
                    project_key=project.key,
                    occurred_at=item.occurred_at,
                    extra={
                        "source_id": item.id,
                        "url": item.url,
                        "permission": item.permission.value,
                    },
                    audit=False,
                )
            except ConflictError:
                outcome.unchanged += 1  # the same text is already indexed elsewhere
                continue
            outcome.updated += current is not None
            outcome.added += current is None
        for (doc_source, source_id), document in existing.items():
            if doc_source == source and source_id not in seen:
                await session.delete(document)
                outcome.removed += 1
        result.sources.append(outcome)
    for source, error in errors.items():
        if source != "warnings":
            result.sources.append(SourceResult(source, error=error))

    await AuditService(session).record(
        actions.KNOWLEDGE_SYNC,
        status=actions.SUCCESS,
        principal=principal,
        resource_type="project_index",
        resource_id=project.key,
        details={
            s.source: {k: v for k, v in vars(s).items() if k != "source" and v}
            for s in result.sources
        },
    )
    await session.commit()
    logger.info(
        "Project indexed",
        extra={
            "project": project.key,
            "sources": {s.source: s.added + s.updated for s in result.sources},
        },
    )
    return result
