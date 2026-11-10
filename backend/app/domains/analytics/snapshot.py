"""Normalized project data used by every analytic (one fetch, many calculations)."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")

_DONE = {"done", "closed", "resolved", "released", "complete", "completed"}
_TODO = {"to do", "todo", "open", "backlog", "new", "selected for development"}
_PRIORITY_WEIGHT = {"highest": 3.0, "critical": 3.0, "blocker": 3.0, "high": 2.0, "medium": 1.0}


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return (
            datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            if "T" in text
            else date.fromisoformat(text[:10])
        )
    except ValueError:
        return None


@dataclass
class IssueFacts:
    key: str
    summary: str
    status: str
    category: str  # todo | in_progress | done
    priority: str
    assignee: str | None
    assignee_id: str | None
    due: date | None
    points: float
    issue_type: str
    blocked_by: list[str] = field(default_factory=list)
    updated: date | None = None
    created: date | None = None

    @property
    def is_open(self) -> bool:
        return self.category != "done"

    @property
    def is_blocked(self) -> bool:
        return self.status.lower() == "blocked" or bool(self.blocked_by)

    @property
    def priority_weight(self) -> float:
        return _PRIORITY_WEIGHT.get(self.priority.lower(), 0.5)


@dataclass
class PullFacts:
    repository: str
    number: int
    title: str
    state: str  # open | closed | merged
    draft: bool
    author: str | None
    created: date | None
    merged: date | None
    requested_reviewers: list[str]
    review_states: list[str]
    issue_keys: list[str]
    url: str | None = None


@dataclass
class RunFacts:
    repository: str
    branch: str | None
    status: str | None
    conclusion: str | None
    created: date | None
    url: str | None = None


@dataclass
class LeaveFacts:
    employee_id: str  # a member id (Keka id, HR id or "name:<name>"), see Member.ids
    start: date
    end: date
    status_code: Any = None
    source: str = "keka"


@dataclass
class Member:
    name: str
    jira_account_id: str | None
    keka_employee_id: str | None
    capacity_points: float
    hr_employee_id: str | None = None  # id in another HR system (BambooHR, Workday, ...)
    github_login: str | None = None  # links PR authors to this person

    @property
    def ids(self) -> set[str]:
        """Every id leave records may carry for this member."""
        ids = {f"name:{self.name.lower()}"}
        ids.update(i for i in (self.keka_employee_id, self.hr_employee_id) if i)
        return ids


@dataclass
class SprintFacts:
    id: int
    name: str
    state: str | None
    start: date | None
    end: date | None
    goal: str | None = None


@dataclass
class OpsFacts:
    """Production signals from ops connectors, scoped by the project's links."""

    incidents: list[Any] = field(default_factory=list)
    errors: list[Any] = field(default_factory=list)
    deployments: list[Any] = field(default_factory=list)
    quality_gates: list[Any] = field(default_factory=list)


@dataclass
class ProjectSnapshot:
    key: str
    name: str
    today: date
    issues: list[IssueFacts] = field(default_factory=list)
    sprint: SprintFacts | None = None
    sprint_issue_keys: set[str] = field(default_factory=set)
    pulls: list[PullFacts] = field(default_factory=list)
    runs: list[RunFacts] = field(default_factory=list)
    leave: list[LeaveFacts] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    default_branch: str = "main"
    warnings: list[str] = field(default_factory=list)
    links: dict[str, list[str]] = field(default_factory=dict)
    ops: OpsFacts | None = None  # None when no ops connector covers this project
    finance: list[Any] | None = None  # BudgetLines when a finance connector covers it
    deals: list[Any] | None = None  # open CRM deals tagged with this project's key

    @property
    def sprint_issues(self) -> list[IssueFacts]:
        if not self.sprint_issue_keys:
            return []
        return [i for i in self.issues if i.key in self.sprint_issue_keys]


# ------------------------------------------------------------------ normalizers


def _category(fields: dict[str, Any]) -> str:
    status = fields.get("status") or {}
    category_key = ((status.get("statusCategory") or {}).get("key") or "").lower()
    if category_key in {"new", "indeterminate", "done"}:
        return {"new": "todo", "indeterminate": "in_progress", "done": "done"}[category_key]
    name = (status.get("name") or "").lower()
    if name in _DONE:
        return "done"
    if name in _TODO:
        return "todo"
    return "in_progress"


def _points(fields: dict[str, Any], points_field: str) -> float:
    for name in (points_field, "story_points", "customfield_10016", "customfield_10026"):
        value = fields.get(name)
        if isinstance(value, int | float):
            return float(value)
    return 0.0


def normalize_issue(raw: dict[str, Any], points_field: str) -> IssueFacts:
    fields = raw.get("fields") or {}
    assignee = fields.get("assignee") or {}
    blocked_by = [
        (link.get("inwardIssue") or {}).get("key")
        for link in fields.get("issuelinks") or []
        if "blocked by" in ((link.get("type") or {}).get("inward") or "").lower()
        and link.get("inwardIssue")
    ]
    return IssueFacts(
        key=raw.get("key", ""),
        summary=fields.get("summary") or "",
        status=(fields.get("status") or {}).get("name") or "Unknown",
        category=_category(fields),
        priority=(fields.get("priority") or {}).get("name") or "Medium",
        assignee=assignee.get("displayName"),
        assignee_id=assignee.get("accountId"),
        due=parse_date(fields.get("duedate")),
        points=_points(fields, points_field),
        issue_type=(fields.get("issuetype") or {}).get("name") or "Task",
        blocked_by=[key for key in blocked_by if key],
        updated=parse_date(fields.get("updated")),
        created=parse_date(fields.get("created")),
    )


def normalize_pull(
    repository: str, raw: dict[str, Any], reviews: list[dict] | None = None
) -> PullFacts:
    text = f"{raw.get('title', '')} {raw.get('head') or ''}"
    return PullFacts(
        repository=repository,
        number=int(raw.get("number") or 0),
        title=raw.get("title") or "",
        state=raw.get("state") or "open",
        draft=bool(raw.get("draft")),
        author=raw.get("author"),
        created=parse_date(raw.get("created_at")),
        merged=parse_date(raw.get("merged_at")),
        requested_reviewers=list(raw.get("requested_reviewers") or []),
        review_states=[r.get("state", "") for r in reviews or raw.get("reviews") or []],
        issue_keys=sorted(set(ISSUE_KEY.findall(text.upper()))),
        url=raw.get("url"),
    )


def normalize_run(repository: str, raw: dict[str, Any]) -> RunFacts:
    return RunFacts(
        repository=repository,
        branch=raw.get("branch"),
        status=raw.get("status"),
        conclusion=raw.get("conclusion"),
        created=parse_date(raw.get("created_at")),
        url=raw.get("url"),
    )


def normalize_leave(raw: dict[str, Any]) -> LeaveFacts | None:
    start, end = parse_date(raw.get("from")), parse_date(raw.get("to"))
    if not (raw.get("employee_id") and start and end):
        return None
    return LeaveFacts(raw["employee_id"], start, end, raw.get("status_code"))
