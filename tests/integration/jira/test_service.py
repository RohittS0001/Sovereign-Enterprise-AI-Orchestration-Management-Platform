from unittest.mock import AsyncMock

import pytest

from backend.app.integrations.jira.service import JiraService
from backend.app.integrations.jira.schemas import CreateEpicRequest
from backend.app.integrations.jira.schemas import CreateStoryRequest
from backend.app.integrations.jira.schemas import CreateTaskRequest
from backend.app.integrations.jira.schemas import AssignIssueRequest
from backend.app.integrations.jira.schemas import UpdateIssueRequest
from backend.app.integrations.jira.schemas import TransitionIssueRequest
from backend.app.integrations.jira.schemas import AddCommentRequest
from backend.app.integrations.jira.schemas import LinkIssuesRequest
@pytest.fixture
def jira_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def jira_service(jira_client: AsyncMock) -> JiraService:
    return JiraService(client=jira_client)


@pytest.mark.asyncio
async def test_get_project(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {
        "id": "10001",
        "key": "TEST",
        "name": "Test Project",
    }

    jira_client.get.return_value = expected

    result = await jira_service.get_project("TEST")

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/project/TEST"
    )


@pytest.mark.asyncio
async def test_get_issue(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {
        "id": "10001",
        "key": "TEST-1",
    }

    jira_client.get.return_value = expected

    result = await jira_service.get_issue("TEST-1")

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-1"
    )


@pytest.mark.asyncio
async def test_search_issues(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {
        "issues": [],
        "total": 0,
    }

    jira_client.get.return_value = expected

    result = await jira_service.search_issues(
        "project = TEST",
        start_at=0,
        max_results=25,
    )

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/search/jql",
        params={
            "jql": "project = TEST",
            "startAt": 0,
            "maxResults": 25,
        },
    )


@pytest.mark.asyncio
async def test_get_project_issues(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    jira_client.get.return_value = {"issues": []}

    result = await jira_service.get_project_issues("TEST")

    assert result == {"issues": []}

    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/search/jql",
        params={
            "jql": "project = TEST ORDER BY created DESC",
            "startAt": 0,
            "maxResults": 50,
        },
    )


@pytest.mark.asyncio
async def test_get_issue_comments(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {"comments": []}

    jira_client.get.return_value = expected

    result = await jira_service.get_issue_comments("TEST-1")

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-1/comment",
        params={
            "startAt": 0,
            "maxResults": 50,
        },
    )


@pytest.mark.asyncio
async def test_get_issue_history(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {"values": []}

    jira_client.get.return_value = expected

    result = await jira_service.get_issue_history("TEST-1")

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-1/changelog",
        params={
            "startAt": 0,
            "maxResults": 50,
        },
    )


@pytest.mark.asyncio
async def test_get_issue_dependencies(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {
        "key": "TEST-1",
        "fields": {
            "issuelinks": [],
        },
    }

    jira_client.get.return_value = expected

    result = await jira_service.get_issue_dependencies("TEST-1")

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-1",
        params={
            "fields": "issuelinks",
        },
    )


@pytest.mark.asyncio
async def test_get_sprint(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {
        "id": 101,
        "name": "Sprint 1",
        "state": "active",
    }

    jira_client.get.return_value = expected

    result = await jira_service.get_sprint(101)

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/agile/1.0/sprint/101"
    )


@pytest.mark.asyncio
async def test_get_sprint_issues(
    jira_client: AsyncMock,
    jira_service: JiraService,
) -> None:
    expected = {
        "issues": [],
        "total": 0,
    }

    jira_client.get.return_value = expected

    result = await jira_service.get_sprint_issues(
        101,
        start_at=10,
        max_results=25,
    )

    assert result == expected
    jira_client.get.assert_awaited_once_with(
        "/rest/agile/1.0/sprint/101/issue",
        params={
            "startAt": 10,
            "maxResults": 25,
        },
    )
@pytest.mark.asyncio
async def test_create_epic() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    expected = {
        "id": "10001",
        "key": "TEST-1",
    }

    client.post.return_value = expected

    request = CreateEpicRequest(
        project_key="TEST",
        summary="Password Management Initiative",
        description="Implement centralized password management.",
    )

    result = await service.create_epic(request)

    assert result == expected

    client.post.assert_awaited_once()

    path, payload = client.post.await_args.args

    assert path == "/rest/api/3/issue"

    assert payload["fields"]["project"]["key"] == "TEST"
    assert payload["fields"]["summary"] == "Password Management Initiative"
    assert payload["fields"]["issuetype"]["name"] == "Epic"
    assert payload["fields"]["description"]["content"][0]["content"][0]["text"] == (
        "Implement centralized password management."
    )


@pytest.mark.asyncio
async def test_create_story() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    expected = {
        "id": "10002",
        "key": "TEST-2",
    }

    client.post.return_value = expected

    request = CreateStoryRequest(
        project_key="TEST",
        summary="Implement password reset flow",
        description="Build the password reset functionality.",
        parent_key="TEST-1",
    )

    result = await service.create_story(request)

    assert result == expected

    client.post.assert_awaited_once()

    path, payload = client.post.await_args.args

    assert path == "/rest/api/3/issue"
    assert payload["fields"]["project"]["key"] == "TEST"
    assert payload["fields"]["summary"] == "Implement password reset flow"
    assert payload["fields"]["issuetype"]["name"] == "Story"
    assert payload["fields"]["parent"]["key"] == "TEST-1"


@pytest.mark.asyncio
async def test_create_task() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    expected = {
        "id": "10003",
        "key": "TEST-3",
    }

    client.post.return_value = expected

    request = CreateTaskRequest(
        project_key="TEST",
        summary="Configure authentication service",
        description="Implement backend authentication configuration.",
        parent_key="TEST-2",
    )

    result = await service.create_task(request)

    assert result == expected

    client.post.assert_awaited_once()

    path, payload = client.post.await_args.args

    assert path == "/rest/api/3/issue"
    assert payload["fields"]["project"]["key"] == "TEST"
    assert payload["fields"]["summary"] == "Configure authentication service"
    assert payload["fields"]["issuetype"]["name"] == "Task"
    assert payload["fields"]["parent"]["key"] == "TEST-2"


@pytest.mark.asyncio
async def test_assign_issue() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    client.put.return_value = {}

    request = AssignIssueRequest(
        issue_key="TEST-3",
        account_id="user-account-123",
    )

    result = await service.assign_issue(request)

    assert result == {
        "issue_key": "TEST-3",
        "account_id": "user-account-123",
        "status": "assigned",
    }

    client.put.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-3/assignee",
        {
            "accountId": "user-account-123",
        },
    )


@pytest.mark.asyncio
async def test_update_issue() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    client.put.return_value = {}

    request = UpdateIssueRequest(
        issue_key="TEST-3",
        summary="Updated authentication task",
        priority_id="3",
        assignee_account_id="user-account-456",
    )

    result = await service.update_issue(request)

    assert result == {
        "issue_key": "TEST-3",
        "updated_fields": [
            "summary",
            "priority",
            "assignee",
        ],
        "status": "updated",
    }

    client.put.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-3",
        {
            "fields": {
                "summary": "Updated authentication task",
                "priority": {
                    "id": "3",
                },
                "assignee": {
                    "accountId": "user-account-456",
                },
            }
        },
    )


@pytest.mark.asyncio
async def test_transition_issue() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    client.post.return_value = {}

    request = TransitionIssueRequest(
        issue_key="TEST-3",
        transition_id="31",
    )

    result = await service.transition_issue(request)

    assert result == {
        "issue_key": "TEST-3",
        "transition_id": "31",
        "status": "transitioned",
    }

    client.post.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-3/transitions",
        {
            "transition": {
                "id": "31",
            }
        },
    )


@pytest.mark.asyncio
async def test_add_comment() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    client.post.return_value = {
        "id": "20001",
    }

    request = AddCommentRequest(
        issue_key="TEST-3",
        comment="Authentication implementation is ready for review.",
    )

    result = await service.add_comment(request)

    assert result == {
        "issue_key": "TEST-3",
        "comment": "Authentication implementation is ready for review.",
        "jira_response": {
            "id": "20001",
        },
        "status": "comment_added",
    }

    client.post.assert_awaited_once_with(
        "/rest/api/3/issue/TEST-3/comment",
        {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "Authentication implementation is ready for review.",
                            }
                        ],
                    }
                ],
            }
        },
    )


@pytest.mark.asyncio
async def test_link_issues() -> None:
    client = AsyncMock()
    service = JiraService(client=client)

    client.post.return_value = {}

    request = LinkIssuesRequest(
        issue_key="TEST-3",
        linked_issue_key="TEST-4",
        link_type="Blocks",
    )

    result = await service.link_issues(request)

    assert result == {
        "issue_key": "TEST-3",
        "linked_issue_key": "TEST-4",
        "link_type": "Blocks",
        "status": "linked",
    }

    client.post.assert_awaited_once_with(
        "/rest/api/3/issueLink",
        {
            "type": {
                "name": "Blocks",
            },
            "inwardIssue": {
                "key": "TEST-3",
            },
            "outwardIssue": {
                "key": "TEST-4",
            },
        },
    )