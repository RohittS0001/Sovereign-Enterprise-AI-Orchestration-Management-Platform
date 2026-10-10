
from unittest.mock import AsyncMock

import pytest

from backend.app.integrations.jira.schemas import (
    AddCommentRequest,
    AssignIssueRequest,
    CreateEpicRequest,
    CreateStoryRequest,
    CreateTaskRequest,
    LinkIssuesRequest,
    TransitionIssueRequest,
    UpdateIssueRequest,
)
from backend.app.mcp.schemas.common import ToolStatus
from backend.app.mcp.tools.jira import JiraTools


@pytest.fixture
def jira_service():
    service = AsyncMock()

    # Read-tool responses
    service.get_project.return_value = {
        "id": "10001",
        "key": "DEMO",
        "name": "Demo Project",
    }
    service.get_issue.return_value = {
        "key": "DEMO-1",
        "fields": {"summary": "Test issue"},
    }
    service.search_issues.return_value = {
        "issues": [{"key": "DEMO-1"}],
        "total": 1,
    }
    service.get_project_issues.return_value = {
        "issues": [{"key": "DEMO-1"}],
        "total": 1,
    }
    service.get_issue_comments.return_value = {"comments": []}
    service.get_issue_history.return_value = {"histories": []}
    service.get_issue_dependencies.return_value = {
        "key": "DEMO-1",
        "fields": {"issuelinks": []},
    }
    service.get_sprint.return_value = {
        "id": 42,
        "name": "Sprint 1",
    }
    service.get_sprint_issues.return_value = {
        "issues": [{"key": "DEMO-1"}],
        "total": 1,
    }

    # Create-tool responses
    service.create_epic.return_value = {
        "id": "10010",
        "key": "DEMO-10",
    }
    service.create_story.return_value = {
        "id": "10011",
        "key": "DEMO-11",
    }
    service.create_task.return_value = {
        "id": "10012",
        "key": "DEMO-12",
    }

    # Additional write-tool responses
    service.assign_issue.return_value = {
        "issue_key": "DEMO-1",
        "account_id": "user-account-123",
        "status": "assigned",
    }
    service.update_issue.return_value = {
        "issue_key": "DEMO-1",
        "status": "updated",
    }
    service.transition_issue.return_value = {
        "issue_key": "DEMO-1",
        "transition_id": "31",
        "status": "transitioned",
    }
    service.add_comment.return_value = {
        "issue_key": "DEMO-1",
        "status": "comment_added",
    }
    service.link_issues.return_value = {
        "issue_key": "DEMO-1",
        "linked_issue_key": "DEMO-2",
        "status": "linked",
    }

    return service


@pytest.fixture
def jira_tools(jira_service):
    return JiraTools(service=jira_service)


# --------------------------------------------------
# Read tools
# --------------------------------------------------

@pytest.mark.asyncio
async def test_get_project(jira_tools, jira_service):
    result = await jira_tools.get_project({"project_key": "DEMO"})

    assert result.status == ToolStatus.SUCCESS
    assert result.data["key"] == "DEMO"
    jira_service.get_project.assert_awaited_once_with("DEMO")


@pytest.mark.asyncio
async def test_get_issue(jira_tools, jira_service):
    result = await jira_tools.get_issue({"issue_key": "DEMO-1"})

    assert result.status == ToolStatus.SUCCESS
    assert result.data["key"] == "DEMO-1"
    jira_service.get_issue.assert_awaited_once_with("DEMO-1")


@pytest.mark.asyncio
async def test_search_issues(jira_tools, jira_service):
    result = await jira_tools.search_issues({
        "jql": "project = DEMO",
        "start_at": 0,
        "max_results": 20,
        "fields": ["summary"],
    })

    assert result.status == ToolStatus.SUCCESS
    assert len(result.data["issues"]) == 1

    jira_service.search_issues.assert_awaited_once_with(
        "project = DEMO",
        start_at=0,
        max_results=20,
        fields=["summary"],
    )


@pytest.mark.asyncio
async def test_get_project_issues(jira_tools, jira_service):
    result = await jira_tools.get_project_issues({
        "project_key": "DEMO",
    })

    assert result.status == ToolStatus.SUCCESS
    jira_service.get_project_issues.assert_awaited_once_with(
        "DEMO",
        start_at=0,
        max_results=50,
    )


@pytest.mark.asyncio
async def test_get_issue_comments(jira_tools, jira_service):
    result = await jira_tools.get_issue_comments({
        "issue_key": "DEMO-1",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data == {"comments": []}

    jira_service.get_issue_comments.assert_awaited_once_with(
        "DEMO-1",
        start_at=0,
        max_results=50,
    )


@pytest.mark.asyncio
async def test_get_issue_history(jira_tools, jira_service):
    result = await jira_tools.get_issue_history({
        "issue_key": "DEMO-1",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data == {"histories": []}

    jira_service.get_issue_history.assert_awaited_once_with(
        "DEMO-1",
        start_at=0,
        max_results=50,
    )


@pytest.mark.asyncio
async def test_get_issue_dependencies(jira_tools, jira_service):
    result = await jira_tools.get_issue_dependencies({
        "issue_key": "DEMO-1",
    })

    assert result.status == ToolStatus.SUCCESS

    jira_service.get_issue_dependencies.assert_awaited_once_with(
        "DEMO-1"
    )


@pytest.mark.asyncio
async def test_get_sprint(jira_tools, jira_service):
    result = await jira_tools.get_sprint({"sprint_id": 42})

    assert result.status == ToolStatus.SUCCESS
    assert result.data["id"] == 42
    jira_service.get_sprint.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_get_sprint_issues(jira_tools, jira_service):
    result = await jira_tools.get_sprint_issues({"sprint_id": 42})

    assert result.status == ToolStatus.SUCCESS

    jira_service.get_sprint_issues.assert_awaited_once_with(
        42,
        start_at=0,
        max_results=50,
    )


# --------------------------------------------------
# Create tools
# --------------------------------------------------

@pytest.mark.asyncio
async def test_create_epic(jira_tools, jira_service):
    result = await jira_tools.create_epic({
        "project_key": "DEMO",
        "summary": "Platform Epic",
        "description": "Build the platform",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["key"] == "DEMO-10"

    jira_service.create_epic.assert_awaited_once_with(
        CreateEpicRequest(
            project_key="DEMO",
            summary="Platform Epic",
            description="Build the platform",
        )
    )


@pytest.mark.asyncio
async def test_create_story(jira_tools, jira_service):
    result = await jira_tools.create_story({
        "project_key": "DEMO",
        "summary": "Implement authentication",
        "parent_key": "DEMO-10",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["key"] == "DEMO-11"

    jira_service.create_story.assert_awaited_once_with(
        CreateStoryRequest(
            project_key="DEMO",
            summary="Implement authentication",
            description=None,
            parent_key="DEMO-10",
        )
    )


@pytest.mark.asyncio
async def test_create_task(jira_tools, jira_service):
    result = await jira_tools.create_task({
        "project_key": "DEMO",
        "summary": "Write unit tests",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["key"] == "DEMO-12"

    jira_service.create_task.assert_awaited_once_with(
        CreateTaskRequest(
            project_key="DEMO",
            summary="Write unit tests",
            description=None,
            parent_key=None,
        )
    )


# --------------------------------------------------
# Assign issue
# --------------------------------------------------

@pytest.mark.asyncio
async def test_assign_issue(jira_tools, jira_service):
    result = await jira_tools.assign_issue({
        "issue_key": "DEMO-1",
        "account_id": "user-account-123",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["issue_key"] == "DEMO-1"

    jira_service.assign_issue.assert_awaited_once_with(
        AssignIssueRequest(
            issue_key="DEMO-1",
            account_id="user-account-123",
        )
    )


@pytest.mark.asyncio
async def test_assign_issue_rejects_invalid_input(
    jira_tools,
    jira_service,
):
    result = await jira_tools.assign_issue({
        "issue_key": "DEMO-1",
        "account_id": "",
    })

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.assign_issue.assert_not_awaited()


# --------------------------------------------------
# Update issue
# --------------------------------------------------

@pytest.mark.asyncio
async def test_update_issue(jira_tools, jira_service):
    result = await jira_tools.update_issue({
        "issue_key": "DEMO-1",
        "summary": "Updated summary",
        "description": "Updated description",
        "priority_id": "2",
        "assignee_account_id": "user-account-123",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["issue_key"] == "DEMO-1"

    jira_service.update_issue.assert_awaited_once_with(
        UpdateIssueRequest(
            issue_key="DEMO-1",
            summary="Updated summary",
            description="Updated description",
            priority_id="2",
            assignee_account_id="user-account-123",
        )
    )


@pytest.mark.asyncio
async def test_update_issue_rejects_invalid_input(
    jira_tools,
    jira_service,
):
    result = await jira_tools.update_issue({
        "issue_key": "",
        "summary": "Updated summary",
    })

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.update_issue.assert_not_awaited()


# --------------------------------------------------
# Transition issue
# --------------------------------------------------

@pytest.mark.asyncio
async def test_transition_issue(jira_tools, jira_service):
    result = await jira_tools.transition_issue({
        "issue_key": "DEMO-1",
        "transition_id": "31",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["issue_key"] == "DEMO-1"

    jira_service.transition_issue.assert_awaited_once_with(
        TransitionIssueRequest(
            issue_key="DEMO-1",
            transition_id="31",
        )
    )


@pytest.mark.asyncio
async def test_transition_issue_rejects_invalid_input(
    jira_tools,
    jira_service,
):
    result = await jira_tools.transition_issue({
        "issue_key": "DEMO-1",
        "transition_id": "",
    })

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.transition_issue.assert_not_awaited()


# --------------------------------------------------
# Add comment
# --------------------------------------------------

@pytest.mark.asyncio
async def test_add_comment(jira_tools, jira_service):
    result = await jira_tools.add_comment({
        "issue_key": "DEMO-1",
        "comment": "Ready for review.",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["issue_key"] == "DEMO-1"

    jira_service.add_comment.assert_awaited_once_with(
        AddCommentRequest(
            issue_key="DEMO-1",
            comment="Ready for review.",
        )
    )


@pytest.mark.asyncio
async def test_add_comment_rejects_invalid_input(
    jira_tools,
    jira_service,
):
    result = await jira_tools.add_comment({
        "issue_key": "DEMO-1",
        "comment": "",
    })

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.add_comment.assert_not_awaited()


# --------------------------------------------------
# Link issues
# --------------------------------------------------

@pytest.mark.asyncio
async def test_link_issues(jira_tools, jira_service):
    result = await jira_tools.link_issues({
        "issue_key": "DEMO-1",
        "linked_issue_key": "DEMO-2",
        "link_type": "Blocks",
    })

    assert result.status == ToolStatus.SUCCESS
    assert result.data["issue_key"] == "DEMO-1"

    jira_service.link_issues.assert_awaited_once_with(
        LinkIssuesRequest(
            issue_key="DEMO-1",
            linked_issue_key="DEMO-2",
            link_type="Blocks",
        )
    )


@pytest.mark.asyncio
async def test_link_issues_rejects_invalid_input(
    jira_tools,
    jira_service,
):
    result = await jira_tools.link_issues({
        "issue_key": "DEMO-1",
        "linked_issue_key": "",
        "link_type": "Blocks",
    })

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.link_issues.assert_not_awaited()


# --------------------------------------------------
# General validation and error handling
# --------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_project_input_does_not_call_service(
    jira_tools,
    jira_service,
):
    result = await jira_tools.get_project({"project_key": ""})

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.get_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_epic_rejects_invalid_input(
    jira_tools,
    jira_service,
):
    result = await jira_tools.create_epic({
        "project_key": "DEMO",
        "summary": "",
    })

    assert result.status == ToolStatus.ERROR
    assert "Invalid Jira input" in result.error
    jira_service.create_epic.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_error_returns_tool_error(
    jira_tools,
    jira_service,
):
    jira_service.get_issue.side_effect = RuntimeError(
        "Jira unavailable"
    )

    result = await jira_tools.get_issue({"issue_key": "DEMO-1"})

    assert result.status == ToolStatus.ERROR
    assert "Jira unavailable" in result.error
