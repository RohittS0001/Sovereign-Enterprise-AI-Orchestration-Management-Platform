import base64
import email
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from backend.app.integrations.demo.services import build_demo_services, load_dataset
from backend.app.integrations.email.client import GmailClient
from backend.app.integrations.jira.schemas import CreateTaskRequest
from backend.app.mcp.schemas.common import ToolStatus
from backend.app.mcp.tools.email import EmailTools

TODAY = date(2026, 11, 9)

# ------------------------------------------------------------------ Gmail drafts


def test_gmail_client_builds_mime_draft() -> None:
    service = MagicMock()
    service.users().drafts().create().execute.return_value = {"id": "d1", "message": {"id": "m1"}}

    GmailClient(service=service).create_draft(
        to=["cfo@bigmerchant.example"], cc=["priya@acme.example"], subject="Refunds API",
        body="Live on the 20th.", thread_id="t-2",
    )  # fmt: skip

    body = service.users().drafts().create.call_args.kwargs["body"]
    assert body["message"]["threadId"] == "t-2"
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(body["message"]["raw"]))
    assert parsed["To"] == "cfo@bigmerchant.example"
    assert parsed["Cc"] == "priya@acme.example"
    assert parsed["Subject"] == "Refunds API"
    assert "Live on the 20th." in parsed.get_payload()


async def test_draft_tool_creates_draft() -> None:
    service = MagicMock()
    service.create_draft.return_value = {"id": "d1", "message": {"id": "m1"}}

    result = await EmailTools(service=service).create_draft(
        {"to": ["a@acme.example"], "subject": "Status", "body": "On track."}
    )

    assert result.status == ToolStatus.SUCCESS
    assert result.data["draft_id"] == "d1"
    service.create_draft.assert_called_once_with(
        to=["a@acme.example"], cc=[], subject="Status", body="On track.", thread_id=None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"to": [], "subject": "s", "body": "b"},
        {"to": ["not-an-email"], "subject": "s", "body": "b"},
        {"to": ["a@acme.example"], "subject": "Hi\r\nBcc: attacker@evil.example", "body": "b"},
    ],
)
async def test_draft_tool_rejects_bad_input(payload) -> None:
    service = MagicMock()

    result = await EmailTools(service=service).create_draft(payload)

    assert result.status == ToolStatus.ERROR
    service.create_draft.assert_not_called()


async def test_draft_tool_enforces_recipient_domains() -> None:
    service = MagicMock()
    tools = EmailTools(service=service, allowed_recipient_domains=["acme.example"])

    result = await tools.create_draft(
        {"to": ["a@acme.example"], "cc": ["leak@evil.example"], "subject": "s", "body": "b"}
    )

    assert result.status == ToolStatus.ERROR
    assert "leak@evil.example" in result.error
    service.create_draft.assert_not_called()


# ------------------------------------------------------------------ demo services


def test_dataset_dates_are_relative_to_today() -> None:
    data = load_dataset(TODAY)
    sprint = data["jira"]["sprints"][0]

    assert sprint["startDate"] == (TODAY - timedelta(days=9)).isoformat()
    assert "{today" not in str({k: v for k, v in data.items() if k != "_comment"})


@pytest.fixture
def demo():
    return build_demo_services(TODAY)


async def test_demo_jira_supports_common_jql(demo) -> None:
    jira = demo["jira"]

    blocked = await jira.search_issues('project = PHX AND status = "Blocked"')
    overdue = await jira.search_issues("project = PHX AND duedate < now() AND status != Done")
    unassigned = await jira.search_issues("assignee is EMPTY")
    sprint = await jira.get_sprint_issues(42)

    assert [i["key"] for i in blocked["issues"]] == ["PHX-7"]
    assert {i["key"] for i in overdue["issues"]} == {"PHX-3", "PHX-7"}
    assert len(sprint["issues"]) == 7
    assert unassigned["issues"]  # unsupported "is" operator is ignored, not an error


async def test_demo_jira_writes_change_memory_only(demo) -> None:
    jira = demo["jira"]

    created = await jira.create_task(CreateTaskRequest(project_key="PHX", summary="Escalate"))
    fetched = await jira.get_issue(created["key"])

    assert fetched["fields"]["summary"] == "Escalate"
    assert build_demo_services(TODAY)["jira"].data["issues"][-1]["key"] != created["key"]


async def test_demo_github_keka_gmail(demo) -> None:
    prs = await demo["github"].search_pull_requests("PHX-7")
    runs = await demo["github"].list_workflow_runs("acme/payments", branch="main")
    leave = await demo["keka"].get_leave_requests(TODAY, TODAY + timedelta(days=7))
    found = demo["email"].search_messages("refunds")

    assert [p["number"] for p in prs["pull_requests"]] == [102]
    assert runs["conclusions"] == {"failure": 2, "success": 1}
    assert {item["employee_id"] for item in leave["leave_requests"]} == {"e-002"}
    assert [m["id"] for m in found["messages"]] == ["m-2"]

    with pytest.raises(ValueError, match="GITHUB_ALLOWED_REPOS"):
        await demo["github"].list_pull_requests("acme/secret")
