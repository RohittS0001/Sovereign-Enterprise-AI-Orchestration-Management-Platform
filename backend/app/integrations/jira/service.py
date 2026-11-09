from collections.abc import Callable
from typing import Any

from backend.app.integrations.jira.client import JiraClient
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
from backend.app.integrations.jira.validators import validate_issue_key, validate_project_key


def to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text in a minimal Atlassian Document Format document."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


class JiraService:
    """Business-level Jira operations built on top of JiraClient."""

    def __init__(
        self,
        client: JiraClient | None = None,
        *,
        client_factory: Callable[[], JiraClient] = JiraClient,
    ) -> None:
        # The real client is created on first use so the service (and the tools that
        # wrap it) can be constructed even when Jira is not configured.
        self._client = client
        self._client_factory = client_factory

    @property
    def client(self) -> JiraClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    # --------------------------------------------------
    # Read operations
    # --------------------------------------------------

    async def get_project(self, project_key: str) -> dict[str, Any]:
        """Get Jira project details."""
        project_key = validate_project_key(project_key)
        return await self.client.get(f"/rest/api/3/project/{project_key}")

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Get a Jira issue by key."""
        issue_key = validate_issue_key(issue_key)
        return await self.client.get(f"/rest/api/3/issue/{issue_key}")

    async def search_issues(
        self,
        jql: str,
        *,
        next_page_token: str | None = None,
        max_results: int = 50,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search Jira issues using JQL.

        ``/rest/api/3/search/jql`` pages with ``nextPageToken`` (it ignores
        ``startAt``); pass the token from the previous response to get the next page.
        """
        params: dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
        }

        if next_page_token:
            params["nextPageToken"] = next_page_token

        if fields:
            params["fields"] = ",".join(fields)

        return await self.client.get("/rest/api/3/search/jql", params=params)

    async def get_project_issues(
        self,
        project_key: str,
        *,
        next_page_token: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get issues belonging to a Jira project."""
        project_key = validate_project_key(project_key)
        jql = f'project = "{project_key}" ORDER BY created DESC'

        return await self.search_issues(
            jql,
            next_page_token=next_page_token,
            max_results=max_results,
        )

    async def get_issue_comments(
        self,
        issue_key: str,
        *,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get comments for a Jira issue."""
        issue_key = validate_issue_key(issue_key)
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}/comment",
            params={"startAt": start_at, "maxResults": max_results},
        )

    async def get_issue_history(
        self,
        issue_key: str,
        *,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get issue changelog/history."""
        issue_key = validate_issue_key(issue_key)
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}/changelog",
            params={"startAt": start_at, "maxResults": max_results},
        )

    async def get_issue_dependencies(self, issue_key: str) -> dict[str, Any]:
        """Get issue details including linked issues/dependencies."""
        issue_key = validate_issue_key(issue_key)
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}",
            params={"fields": "issuelinks"},
        )

    async def get_sprint(self, sprint_id: int) -> dict[str, Any]:
        """Get Jira sprint details."""
        return await self.client.get(f"/rest/agile/1.0/sprint/{int(sprint_id)}")

    async def get_sprint_issues(
        self,
        sprint_id: int,
        *,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get issues belonging to a Jira sprint."""
        return await self.client.get(
            f"/rest/agile/1.0/sprint/{int(sprint_id)}/issue",
            params={"startAt": start_at, "maxResults": max_results},
        )

    # --------------------------------------------------
    # Write operations
    # --------------------------------------------------

    async def _create_issue(
        self,
        issue_type: str,
        project_key: str,
        summary: str,
        description: str | None,
        parent_key: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": validate_project_key(project_key)},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }

        if description:
            fields["description"] = to_adf(description)

        if parent_key:
            fields["parent"] = {"key": validate_issue_key(parent_key)}

        return await self.client.post("/rest/api/3/issue", {"fields": fields})

    async def create_epic(self, request: CreateEpicRequest) -> dict[str, Any]:
        """Create a Jira epic."""
        return await self._create_issue(
            "Epic", request.project_key, request.summary, request.description
        )

    async def create_story(self, request: CreateStoryRequest) -> dict[str, Any]:
        """Create a Jira story."""
        return await self._create_issue(
            "Story",
            request.project_key,
            request.summary,
            request.description,
            request.parent_key,
        )

    async def create_task(self, request: CreateTaskRequest) -> dict[str, Any]:
        """Create a Jira task."""
        return await self._create_issue(
            "Task",
            request.project_key,
            request.summary,
            request.description,
            request.parent_key,
        )

    async def assign_issue(self, request: AssignIssueRequest) -> dict[str, Any]:
        """Assign a Jira issue to a user."""
        issue_key = validate_issue_key(request.issue_key)

        await self.client.put(
            f"/rest/api/3/issue/{issue_key}/assignee",
            {"accountId": request.account_id},
        )

        return {
            "issue_key": issue_key,
            "account_id": request.account_id,
            "status": "assigned",
        }

    async def update_issue(self, request: UpdateIssueRequest) -> dict[str, Any]:
        """Update supported fields on a Jira issue."""
        issue_key = validate_issue_key(request.issue_key)
        fields: dict[str, Any] = {}

        if request.summary is not None:
            fields["summary"] = request.summary

        if request.description is not None:
            fields["description"] = to_adf(request.description)

        if request.priority_id is not None:
            fields["priority"] = {"id": request.priority_id}

        if request.assignee_account_id is not None:
            fields["assignee"] = {"accountId": request.assignee_account_id}

        if not fields:
            raise ValueError("At least one issue field must be provided for update")

        await self.client.put(f"/rest/api/3/issue/{issue_key}", {"fields": fields})

        return {
            "issue_key": issue_key,
            "updated_fields": list(fields.keys()),
            "status": "updated",
        }

    async def transition_issue(self, request: TransitionIssueRequest) -> dict[str, Any]:
        """Transition a Jira issue to another workflow state."""
        issue_key = validate_issue_key(request.issue_key)

        await self.client.post(
            f"/rest/api/3/issue/{issue_key}/transitions",
            {"transition": {"id": request.transition_id}},
        )

        return {
            "issue_key": issue_key,
            "transition_id": request.transition_id,
            "status": "transitioned",
        }

    async def add_comment(self, request: AddCommentRequest) -> dict[str, Any]:
        """Add a comment to a Jira issue."""
        issue_key = validate_issue_key(request.issue_key)

        result = await self.client.post(
            f"/rest/api/3/issue/{issue_key}/comment",
            {"body": to_adf(request.comment)},
        )

        return {
            "issue_key": issue_key,
            "comment": request.comment,
            "jira_response": result,
            "status": "comment_added",
        }

    async def link_issues(self, request: LinkIssuesRequest) -> dict[str, Any]:
        """Create a link between two Jira issues."""
        issue_key = validate_issue_key(request.issue_key)
        linked_issue_key = validate_issue_key(request.linked_issue_key)

        await self.client.post(
            "/rest/api/3/issueLink",
            {
                "type": {"name": request.link_type},
                "inwardIssue": {"key": issue_key},
                "outwardIssue": {"key": linked_issue_key},
            },
        )

        return {
            "issue_key": issue_key,
            "linked_issue_key": linked_issue_key,
            "link_type": request.link_type,
            "status": "linked",
        }
