"""Contract: every integration tool succeeds on the demo twins with realistic arguments.

Keeps the demo services (DEMO_MODE, evaluation) in step with the tool catalog: a new tool
without a sample here, or a demo twin missing a method, fails this test.
"""

from datetime import date, timedelta

import pytest

from backend.app.core.config import Settings
from backend.app.tools.base import ToolStatus
from backend.app.tools.catalog import build_tool_registry

TODAY = date.today()
WEEK = {"from_date": (TODAY - timedelta(days=7)).isoformat(), "to_date": TODAY.isoformat()}
REPO = {"repository": "acme/payments"}

SAMPLES: dict[str, dict] = {
    "github.add_pr_comment": {**REPO, "number": 98, "body": "LGTM"},
    "github.get_commit_checks": {**REPO, "ref": "main"},
    "github.get_pull_request": {**REPO, "number": 98},
    "github.list_commits": REPO,
    "github.list_pull_requests": {**REPO, "state": "all"},
    "github.list_workflow_runs": REPO,
    "github.search_pull_requests": {"query": "PHX"},
    "gmail.create_draft": {"to": ["pm@example.com"], "subject": "Status", "body": "Hi"},
    "gmail.get_email": {"message_id": "m-1"},
    "gmail.search_emails": {"query": "PHX"},
    "jira.add_comment": {"issue_key": "PHX-1", "comment": "Looks good"},
    "jira.assign_issue": {"issue_key": "PHX-2", "account_id": "acc-priya"},
    "jira.create_epic": {"project_key": "PHX", "summary": "Payments v2"},
    "jira.create_story": {"project_key": "PHX", "summary": "Refund flow"},
    "jira.create_task": {"project_key": "PHX", "summary": "Write runbook"},
    "jira.get_issue": {"issue_key": "PHX-1"},
    "jira.get_issue_comments": {"issue_key": "PHX-1"},
    "jira.get_issue_dependencies": {"issue_key": "PHX-1"},
    "jira.get_issue_history": {"issue_key": "PHX-1"},
    "jira.get_project": {"project_key": "PHX"},
    "jira.get_project_issues": {"project_key": "PHX"},
    "jira.get_sprint": {"sprint_id": 42},
    "jira.get_sprint_issues": {"sprint_id": 42},
    "jira.link_issues": {"issue_key": "PHX-1", "linked_issue_key": "PHX-2", "link_type": "Blocks"},
    "jira.search_issues": {"jql": "project = PHX AND status != Done"},
    "jira.transition_issue": {"issue_key": "PHX-3", "transition_id": "31"},
    "jira.update_issue": {"issue_key": "PHX-3", "summary": "Retry failed captures"},
    "keka.get_attendance": WEEK,
    "keka.get_employee": {"employee_id": "e-001"},
    "keka.get_leave_balances": {},
    "keka.get_leave_requests": WEEK,
    "keka.search_employees": {"search": "Priya"},
}


@pytest.fixture(scope="module")
def registry():
    return build_tool_registry(Settings(_env_file=None, environment="test", demo_mode=True))


def test_every_integration_tool_has_a_sample(registry) -> None:
    integration_tools = {
        n for n in registry.list_tools() if n.split(".")[0] in {"jira", "github", "keka", "gmail"}
    }
    assert integration_tools == set(SAMPLES)


@pytest.mark.parametrize("name", sorted(SAMPLES))
async def test_tool_succeeds_on_demo_data(registry, name: str) -> None:
    result = await registry.invoke(name, SAMPLES[name])

    assert result.status == ToolStatus.SUCCESS, result.error
    assert result.data is not None


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("jira.get_issue", {"issue_key": "PHX-999"}),
        ("github.get_pull_request", {**REPO, "number": 99999}),
        ("keka.get_employee", {"employee_id": "e-404"}),
        ("keka.get_leave_requests", {"from_date": "2026-02-10", "to_date": "2026-01-01"}),
        ("github.list_pull_requests", {"repository": "not a repo"}),
    ],
)
async def test_bad_requests_return_errors_not_exceptions(registry, name, payload) -> None:
    result = await registry.invoke(name, payload)

    assert result.status == ToolStatus.ERROR and result.error
