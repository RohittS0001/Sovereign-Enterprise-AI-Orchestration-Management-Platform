"""Gather a ProjectSnapshot from the configured integrations.

Each source is fetched independently; a failing integration adds a warning instead of
failing the whole analysis, so a Keka outage still leaves Jira/GitHub insight intact.
"""

import time
from datetime import date, timedelta
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.logging import get_logger
from backend.app.db.models import Project
from backend.app.domains.analytics.snapshot import (
    LeaveFacts,
    Member,
    OpsFacts,
    ProjectSnapshot,
    SprintFacts,
    normalize_issue,
    normalize_leave,
    normalize_pull,
    normalize_run,
    parse_date,
)
from backend.app.integrations.hub import IntegrationHub

logger = get_logger(__name__)

_JIRA_FIELDS = ["summary", "status", "assignee", "priority", "duedate", "issuetype",
                "issuelinks", "updated", "created"]  # fmt: skip


def _error(exc: Exception) -> str:
    return str(getattr(exc, "message", None) or exc.__class__.__name__)[:200]


PULL_LIMIT = 50  # most recent pull requests per repository


class SnapshotCollector:
    def __init__(self, hub: IntegrationHub, settings: Settings) -> None:
        self.hub = hub
        self.settings = settings
        self._cache: dict[str, tuple[float, ProjectSnapshot]] = {}

    async def collect(
        self, project: Project, *, today: date | None = None, fresh: bool = False
    ) -> ProjectSnapshot:
        cached = self._cache.get(project.key)
        ttl = self.settings.analytics_cache_seconds
        if cached and not fresh and today is None and time.monotonic() - cached[0] < ttl:
            return cached[1]

        snapshot = ProjectSnapshot(
            key=project.key,
            name=project.name,
            today=today or date.today(),
            default_branch=project.default_branch,
            links=dict(project.links or {}),
            members=[
                Member(
                    name=m.get("name") or m.get("jira_account_id") or "unknown",
                    jira_account_id=m.get("jira_account_id"),
                    keka_employee_id=m.get("keka_employee_id"),
                    hr_employee_id=m.get("hr_employee_id"),
                    github_login=m.get("github_login"),
                    capacity_points=float(
                        m.get("capacity_points") or self.settings.analytics_default_capacity_points
                    ),
                )
                for m in project.members
            ],
        )
        await self._jira(project, snapshot)
        await self._github(project, snapshot)
        await self._keka(snapshot)
        await self._ops(snapshot)
        await self._hr(snapshot)
        await self._finance(snapshot)
        await self._crm(snapshot)

        if today is None:
            self._cache[project.key] = (time.monotonic(), snapshot)
        return snapshot

    async def _jira(self, project: Project, snapshot: ProjectSnapshot) -> None:
        jira = self.hub.jira
        if jira is None:
            snapshot.warnings.append("Jira is not connected; issue metrics unavailable")
            return
        points_field = self.settings.jira_story_points_field
        try:
            result = await jira.search_issues(
                f'project = "{project.key}" ORDER BY updated DESC',
                max_results=100,
                fields=[*_JIRA_FIELDS, points_field],
            )
            snapshot.issues = [normalize_issue(i, points_field) for i in result.get("issues", [])]
            if not result.get("isLast", True):
                snapshot.incomplete.add("jira")
                snapshot.warnings.append("Only the 100 most recently updated issues were analysed")
        except Exception as exc:
            snapshot.incomplete.add("jira")
            snapshot.warnings.append(f"Jira issues unavailable: {_error(exc)}")
            return

        if project.active_sprint_id is None:
            snapshot.warnings.append("No active sprint configured; sprint forecast skipped")
            return
        try:
            sprint: dict[str, Any] = await jira.get_sprint(project.active_sprint_id)
            sprint_issues = await jira.get_sprint_issues(project.active_sprint_id, max_results=100)
            snapshot.sprint = SprintFacts(
                id=int(sprint.get("id", project.active_sprint_id)),
                name=sprint.get("name") or f"Sprint {project.active_sprint_id}",
                state=sprint.get("state"),
                start=parse_date(sprint.get("startDate")),
                end=parse_date(sprint.get("endDate")),
                goal=sprint.get("goal"),
            )
            snapshot.sprint_issue_keys = {i.get("key") for i in sprint_issues.get("issues", [])}
            known = {i.key for i in snapshot.issues}
            snapshot.issues.extend(
                normalize_issue(i, points_field)
                for i in sprint_issues.get("issues", [])
                if i.get("key") not in known
            )
        except Exception as exc:
            snapshot.incomplete.add("jira")
            snapshot.warnings.append(f"Sprint data unavailable: {_error(exc)}")

    async def _github(self, project: Project, snapshot: ProjectSnapshot) -> None:
        github = self.hub.github
        if not project.github_repos:
            return
        if github is None:
            snapshot.warnings.append(
                "GitHub is not connected; delivery pipeline metrics unavailable"
            )
            return
        for repo in project.github_repos:
            try:
                pulls = await github.list_pull_requests(repo, state="all", limit=PULL_LIMIT)
                listed = pulls.get("pull_requests", [])
                if len(listed) >= PULL_LIMIT:  # older pull requests exist beyond the page
                    snapshot.incomplete.add("github")
                snapshot.pulls.extend(normalize_pull(repo, p) for p in listed)
                runs = await github.list_workflow_runs(
                    repo, branch=project.default_branch, limit=10
                )
                snapshot.runs.extend(normalize_run(repo, r) for r in runs.get("workflow_runs", []))
            except Exception as exc:
                snapshot.incomplete.add("github")
                snapshot.warnings.append(f"GitHub data for {repo} unavailable: {_error(exc)}")

    async def _keka(self, snapshot: ProjectSnapshot) -> None:
        ids = [m.keka_employee_id for m in snapshot.members if m.keka_employee_id]
        if not ids:
            return
        if self.hub.keka is None:
            snapshot.warnings.append("Keka is not connected; leave is not reflected in capacity")
            return
        horizon_end = snapshot.sprint.end if snapshot.sprint and snapshot.sprint.end else None
        end = max(horizon_end or snapshot.today, snapshot.today) + timedelta(days=14)
        try:
            result = await self.hub.keka.get_leave_requests(snapshot.today, end, ids)
            snapshot.leave = [
                leave for raw in result.get("leave_requests", []) if (leave := normalize_leave(raw))
            ]
        except Exception as exc:
            snapshot.warnings.append(f"Keka leave unavailable: {_error(exc)}")

    async def _ops(self, snapshot: ProjectSnapshot) -> None:
        """Incidents, errors, deployments and quality gates for the project's linked records."""
        from backend.app.integrations.connectors.ops import PROJECT_LINKS, capabilities

        services = self.hub.by_category("ops")
        facts = OpsFacts()
        covered = False
        for source, service in services.items():
            scope = snapshot.links.get(PROJECT_LINKS.get(source, ""), [])
            if not scope:
                continue  # never attribute org-wide signals to a project without links
            covered = True
            try:
                for capability in capabilities(service):
                    if capability == "list_incidents":
                        facts.incidents += await service.list_incidents(open_only=True, scope=scope)
                    elif capability == "list_errors":
                        facts.errors += await service.list_errors(scope=scope)
                    elif capability == "list_deployments":
                        facts.deployments += await service.list_deployments(scope=scope)
                    else:
                        facts.quality_gates += await service.quality_gates(scope=scope)
            except Exception as exc:
                snapshot.warnings.append(f"{source} data unavailable: {_error(exc)}")
        if covered:
            snapshot.ops = facts

    async def _hr(self, snapshot: ProjectSnapshot) -> None:
        """Approved leave from other HR systems, matched to members by HR id or name."""
        services = self.hub.by_category("hr")
        if not services or not snapshot.members:
            return
        sprint_end = snapshot.sprint.end if snapshot.sprint and snapshot.sprint.end else None
        end = max(sprint_end or snapshot.today, snapshot.today) + timedelta(days=14)
        by_id: dict[str, Member | None] = {}
        for m in snapshot.members:
            for i in m.ids:
                # Two members sharing a name (or an id): ambiguous, so never matched by it.
                by_id[i.lower()] = None if i.lower() in by_id else m
        ambiguous = sorted(k.removeprefix("name:") for k, v in by_id.items() if v is None)
        if ambiguous:
            snapshot.warnings.append(
                "Leave cannot be matched by name for "
                + ", ".join(ambiguous)
                + " (several members share it): set hr_employee_id / keka_employee_id"
            )
        for source, service in services.items():
            try:
                records = await service.list_leave(snapshot.today, end)
            except Exception as exc:
                snapshot.warnings.append(f"{source} leave unavailable: {_error(exc)}")
                continue
            for record in records:
                member = by_id.get(record.employee_id.lower()) or by_id.get(
                    f"name:{' '.join((record.name or '').split()).lower()}"
                )
                if member is not None:
                    member_id = next(
                        iter(sorted(member.ids - {f"name:{member.name.lower()}"})), None
                    )
                    snapshot.leave.append(LeaveFacts(
                        member_id or f"name:{member.name.lower()}", record.start, record.end,
                        record.status, source))  # fmt: skip

    async def _finance(self, snapshot: ProjectSnapshot) -> None:
        """Budget vs actuals for the project's cost centres (default: its key and name)."""
        services = self.hub.by_category("finance")
        if not services:
            return
        scope = snapshot.links.get("cost_centers") or [snapshot.key, snapshot.name]
        lines: list[Any] = []
        for source, service in services.items():
            try:
                lines += await service.budget_lines(scope=scope)
            except Exception as exc:
                snapshot.warnings.append(f"{source} budget unavailable: {_error(exc)}")
        if lines:
            snapshot.finance = lines

    async def _crm(self, snapshot: ProjectSnapshot) -> None:
        """Open deals (customer commitments) tagged with the project's key."""
        services = self.hub.by_category("crm")
        if not services:
            return
        deals: list[Any] = []
        for source, service in services.items():
            try:
                deals += await service.list_deals(project=snapshot.key)
            except Exception as exc:
                snapshot.warnings.append(f"{source} deals unavailable: {_error(exc)}")
        snapshot.deals = sorted(deals, key=lambda d: d.close_date or snapshot.today)
