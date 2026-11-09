"""In-memory stand-ins for the Jira, GitHub, Keka and Gmail services.

They expose the same methods as the real services, so the real MCP tools, catalog,
gateway and agent run unchanged on top of them. Used by DEMO_MODE and evaluation.
Writes change the in-memory copy only.
"""

import base64
import copy
import json
import re
from datetime import date, timedelta
from importlib import resources
from typing import Any

from backend.app.integrations.github.service import validate_repository
from backend.app.integrations.jira.validators import validate_issue_key, validate_project_key
from backend.app.integrations.keka.service import validate_employee_ids, validate_range

_TODAY = re.compile(r"\{today(?:([+-])(\d+))?\}")


def _resolve(value: Any, today: date) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            days = int(m.group(2) or 0) * (-1 if m.group(1) == "-" else 1)
            return (today + timedelta(days=days)).isoformat()

        return _TODAY.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve(v, today) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, today) for v in value]
    return value


def load_dataset(today: date | None = None) -> dict[str, Any]:
    raw = (
        resources.files("backend.app.integrations.demo").joinpath("dataset.json").read_text("utf-8")
    )
    return _resolve(json.loads(raw), today or date.today())


# ------------------------------------------------------------------ Jira

_CLAUSE = re.compile(
    r"^\s*(?P<field>\w+)\s*(?P<op>!=|=|in|not in|<|>)\s*(?P<value>.+?)\s*$", re.IGNORECASE
)


class DemoJiraService:
    def __init__(self, data: dict[str, Any], today: date) -> None:
        self.data = data
        self.today = today
        self._next_number = 100

    def _issue(self, key: str) -> dict[str, Any]:
        for issue in self.data["issues"]:
            if issue["key"].upper() == key.upper():
                return issue
        raise ValueError(f"Issue {key} does not exist")

    def _matches(self, issue: dict[str, Any], clause: str) -> bool:
        match = _CLAUSE.match(clause)
        if not match:
            return True  # unsupported clause: ignore rather than fail the demo
        field, op = match["field"].lower(), match["op"].lower()
        raw_value = match["value"].strip()
        values = [v.strip().strip("\"'").lower() for v in raw_value.strip("()").split(",")]
        fields = issue["fields"]

        if field == "duedate":
            due = fields.get("duedate")
            if not due:
                return False
            target = self.today.isoformat() if "now()" in raw_value.lower() else values[0]
            return due < target if op == "<" else due > target if op == ">" else due == target

        actual = {
            "project": issue["key"].split("-")[0],
            "key": issue["key"],
            "status": (fields.get("status") or {}).get("name"),
            "priority": (fields.get("priority") or {}).get("name"),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "issuetype": (fields.get("issuetype") or {}).get("name"),
            "type": (fields.get("issuetype") or {}).get("name"),
            "sprint": str(fields.get("sprint") or ""),
        }.get(field)
        if actual is None and field not in {"assignee", "sprint"}:
            return True
        actual_l = (actual or "empty").lower()
        if values == ["empty"] or values == ["null"]:
            return (not actual) if op in {"=", "in"} else bool(actual)
        if op in {"=", "in"}:
            return actual_l in values
        if op in {"!=", "not in"}:
            return actual_l not in values
        return True

    async def search_issues(
        self, jql: str, *, next_page_token: str | None = None, max_results: int = 50,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        query = re.split(r"\border\s+by\b", jql, flags=re.IGNORECASE)[0]
        clauses = [c for c in re.split(r"\band\b", query, flags=re.IGNORECASE) if c.strip()]
        issues = [i for i in self.data["issues"] if all(self._matches(i, c) for c in clauses)]
        return {"issues": copy.deepcopy(issues[:max_results]), "isLast": True}

    async def get_project(self, project_key: str) -> dict[str, Any]:
        key = validate_project_key(project_key).upper()
        for project in self.data["projects"]:
            if project["key"] == key:
                return copy.deepcopy(project)
        raise ValueError(f"Project {key} does not exist")

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        return copy.deepcopy(self._issue(validate_issue_key(issue_key)))

    async def get_project_issues(
        self, project_key: str, *, next_page_token: str | None = None, max_results: int = 50
    ) -> dict[str, Any]:
        return await self.search_issues(
            f'project = "{validate_project_key(project_key)}"', max_results=max_results
        )

    async def get_issue_comments(self, issue_key: str, **_: Any) -> dict[str, Any]:
        key = self._issue(validate_issue_key(issue_key))["key"]
        comments = self.data["comments"].get(key, [])
        return {"comments": copy.deepcopy(comments), "total": len(comments)}

    async def get_issue_history(self, issue_key: str, **_: Any) -> dict[str, Any]:
        key = self._issue(validate_issue_key(issue_key))["key"]
        return {"values": copy.deepcopy(self.data["history"].get(key, []))}

    async def get_issue_dependencies(self, issue_key: str) -> dict[str, Any]:
        issue = self._issue(validate_issue_key(issue_key))
        return {
            "key": issue["key"],
            "fields": {"issuelinks": issue["fields"].get("issuelinks", [])},
        }

    async def get_sprint(self, sprint_id: int) -> dict[str, Any]:
        for sprint in self.data["sprints"]:
            if sprint["id"] == int(sprint_id):
                return copy.deepcopy(sprint)
        raise ValueError(f"Sprint {sprint_id} does not exist")

    async def get_sprint_issues(self, sprint_id: int, **_: Any) -> dict[str, Any]:
        await self.get_sprint(sprint_id)
        return await self.search_issues(f"sprint = {int(sprint_id)}")

    async def _create(self, issue_type: str, request: Any) -> dict[str, Any]:
        await self.get_project(request.project_key)
        self._next_number += 1
        key = f"{request.project_key.upper()}-{self._next_number}"
        self.data["issues"].append(
            {"key": key, "fields": {"summary": request.summary, "issuetype": {"name": issue_type},
             "status": {"name": "To Do"}, "description": request.description,
             "created": self.today.isoformat(), "updated": self.today.isoformat()}}
        )  # fmt: skip
        return {"id": str(10000 + self._next_number), "key": key}

    async def create_epic(self, request: Any) -> dict[str, Any]:
        return await self._create("Epic", request)

    async def create_story(self, request: Any) -> dict[str, Any]:
        return await self._create("Story", request)

    async def create_task(self, request: Any) -> dict[str, Any]:
        return await self._create("Task", request)

    async def assign_issue(self, request: Any) -> dict[str, Any]:
        issue = self._issue(request.issue_key)
        user = next((u for u in self.data["users"] if u["accountId"] == request.account_id), None)
        if user is None:
            raise ValueError(f"Unknown account id {request.account_id}")
        issue["fields"]["assignee"] = dict(user)
        return {"issue_key": issue["key"], "account_id": request.account_id, "status": "assigned"}

    async def update_issue(self, request: Any) -> dict[str, Any]:
        issue = self._issue(request.issue_key)
        changes = request.model_dump(exclude={"issue_key"}, exclude_none=True)
        if not changes:
            raise ValueError("At least one issue field must be provided for update")
        issue["fields"].update(
            {k: v for k, v in changes.items() if k in {"summary", "description"}}
        )
        return {"issue_key": issue["key"], "updated_fields": sorted(changes), "status": "updated"}

    async def transition_issue(self, request: Any) -> dict[str, Any]:
        issue = self._issue(request.issue_key)
        names = {"11": "To Do", "21": "In Progress", "31": "In Review", "41": "Done"}
        issue["fields"]["status"] = {"name": names.get(request.transition_id, "In Progress")}
        return {
            "issue_key": issue["key"],
            "transition_id": request.transition_id,
            "status": "transitioned",
        }

    async def add_comment(self, request: Any) -> dict[str, Any]:
        key = self._issue(request.issue_key)["key"]
        self.data["comments"].setdefault(key, []).append(
            {
                "author": "Sovereign Agent",
                "created": self.today.isoformat(),
                "body": request.comment,
            }
        )
        return {"issue_key": key, "comment": request.comment, "status": "comment_added"}

    async def link_issues(self, request: Any) -> dict[str, Any]:
        self._issue(request.issue_key)
        self._issue(request.linked_issue_key)
        return {
            "issue_key": request.issue_key,
            "linked_issue_key": request.linked_issue_key,
            "link_type": request.link_type,
            "status": "linked",
        }


# ------------------------------------------------------------------ GitHub


class DemoGitHubService:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.allowed_repos = data["repositories"]

    def _repo(self, repository: str) -> str:
        validate_repository(repository, self.allowed_repos)
        return next(r for r in self.allowed_repos if r.lower() == repository.lower())

    def _pulls(self, repository: str) -> list[dict[str, Any]]:
        return self.data["pull_requests"].get(self._repo(repository), [])

    @staticmethod
    def _public(pr: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in pr.items() if k != "reviews"}

    async def list_pull_requests(
        self, repository: str, *, state: str = "open", base: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        pulls = [
            self._public(p)
            for p in self._pulls(repository)
            if (state == "all" or (state == "open") == (p["state"] == "open"))
            and (base is None or p["base"] == base)
        ]
        return {"repository": repository, "pull_requests": pulls[:limit]}

    async def get_pull_request(self, repository: str, number: int) -> dict[str, Any]:
        for pr in self._pulls(repository):
            if pr["number"] == int(number):
                return {
                    "repository": repository,
                    "pull_request": self._public(pr),
                    "reviews": pr["reviews"],
                }
        raise ValueError(f"Pull request #{number} not found in {repository}")

    async def search_pull_requests(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        terms = [t.lower() for t in query.split() if ":" not in t]
        want_open = "is:open" in query.lower()
        found = [
            {**self._public(pr), "repository": repo}
            for repo, pulls in self.data["pull_requests"].items()
            for pr in pulls
            if all(t in pr["title"].lower() for t in terms)
            and (not want_open or pr["state"] == "open")
        ]
        return {"total_count": len(found), "pull_requests": found[:limit]}

    async def list_commits(self, repository: str, *, limit: int = 30, **_: Any) -> dict[str, Any]:
        commits = self.data["commits"].get(self._repo(repository), [])
        return {"repository": repository, "commits": commits[:limit]}

    async def list_workflow_runs(
        self,
        repository: str,
        *,
        branch: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        runs = [
            r
            for r in self.data["workflow_runs"].get(self._repo(repository), [])
            if (branch is None or r["branch"] == branch)
            and (status is None or status in {r["status"], r["conclusion"]})
        ][:limit]
        conclusions: dict[str, int] = {}
        for run in runs:
            key = run["conclusion"] or run["status"]
            conclusions[key] = conclusions.get(key, 0) + 1
        return {"repository": repository, "total_count": len(runs), "conclusions": conclusions,
                "workflow_runs": runs}  # fmt: skip

    async def get_commit_checks(self, repository: str, ref: str) -> dict[str, Any]:
        checks = self.data["checks"].get(f"{self._repo(repository)}@{ref}", [])
        summary: dict[str, int] = {}
        for check in checks:
            summary[check["conclusion"]] = summary.get(check["conclusion"], 0) + 1
        return {"repository": repository, "ref": ref, "summary": summary,
                "failing": [c["name"] for c in checks if c["conclusion"] == "failure"],
                "checks": checks}  # fmt: skip

    async def add_pr_comment(self, repository: str, number: int, body: str) -> dict[str, Any]:
        await self.get_pull_request(repository, number)
        return {
            "repository": repository,
            "number": number,
            "comment_id": 1,
            "status": "comment_added",
        }


# ------------------------------------------------------------------ Keka


def _overlaps(item: dict[str, Any], start: date, end: date) -> bool:
    return item["from"] <= end.isoformat() and item["to"] >= start.isoformat()


class DemoKekaService:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    async def search_employees(
        self, search: str | None = None, *, employment_status: str = "Working", limit: int = 50
    ) -> dict[str, Any]:
        term = (search or "").lower()
        found = [
            e for e in self.data["employees"]
            if not term or term in e["name"].lower() or term in e["email"].lower()
            or term in e["employee_number"].lower()
        ]  # fmt: skip
        return {"total": len(found), "employees": copy.deepcopy(found[:limit])}

    async def get_employee(self, employee_id: str) -> dict[str, Any]:
        validate_employee_ids([employee_id])
        for employee in self.data["employees"]:
            if employee["id"] == employee_id:
                return copy.deepcopy(employee)
        raise ValueError(f"Employee {employee_id} not found")

    def _filter(self, items: list[dict], ids: list[str] | None) -> list[dict]:
        validate_employee_ids(ids)
        return [i for i in items if not ids or i["employee_id"] in ids]

    async def get_leave_requests(
        self,
        from_date: date,
        to_date: date,
        employee_ids: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        validate_range(from_date, to_date)
        items = [i for i in self._filter(self.data["leave_requests"], employee_ids)
                 if _overlaps(i, from_date, to_date)]  # fmt: skip
        return {"total": len(items), "leave_requests": items[:limit]}

    async def get_leave_balances(
        self, employee_ids: list[str] | None = None, limit: int = 100
    ) -> dict[str, Any]:
        items = self._filter(self.data["leave_balances"], employee_ids)
        return {"total": len(items), "leave_balances": items[:limit]}

    async def get_attendance(
        self,
        from_date: date,
        to_date: date,
        employee_ids: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        validate_range(from_date, to_date)
        items = [i for i in self._filter(self.data["attendance"], employee_ids)
                 if from_date.isoformat() <= i["date"] <= to_date.isoformat()]  # fmt: skip
        return {"total": len(items), "attendance": items[:limit]}


# ------------------------------------------------------------------ Gmail (sync, like EmailService)


class DemoEmailService:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.drafts: list[dict[str, Any]] = []

    def get_profile(self) -> dict[str, Any]:
        return dict(self.data["profile"])

    def search_messages(
        self, query: str = "", max_results: int = 10, page_token: str | None = None
    ) -> dict[str, Any]:
        terms = [
            t.split(":", 1)[-1].lower() for t in query.split() if not t.startswith("newer_than")
        ]
        found = [
            {"id": m["id"], "threadId": m["threadId"]}
            for m in self.data["messages"]
            if all(t in f"{m['from']} {m['subject']} {m['body']}".lower() for t in terms)
        ]
        return {"messages": found[:max_results], "resultSizeEstimate": len(found)}

    def get_message(self, message_id: str) -> dict[str, Any]:
        for m in self.data["messages"]:
            if m["id"] == message_id:
                encoded = base64.urlsafe_b64encode(m["body"].encode()).decode().rstrip("=")
                return {
                    "id": m["id"],
                    "threadId": m["threadId"],
                    "snippet": m["body"][:80],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": m["from"]},
                            {"name": "Subject", "value": m["subject"]},
                            {"name": "Date", "value": m["date"]},
                        ],
                        "body": {"data": encoded},
                    },
                }
        raise ValueError(f"Message {message_id} not found")

    def create_draft(self, **fields: Any) -> dict[str, Any]:
        draft = {
            "id": f"draft-{len(self.drafts) + 1}",
            "message": {"id": f"dm-{len(self.drafts) + 1}"},
        }
        self.drafts.append({**draft, **fields})
        return draft


def build_demo_services(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    data = load_dataset(today)
    return {
        "jira": DemoJiraService(data["jira"], today),
        "github": DemoGitHubService(data["github"]),
        "keka": DemoKekaService(data["keka"]),
        "email": DemoEmailService(data["gmail"]),
    }
