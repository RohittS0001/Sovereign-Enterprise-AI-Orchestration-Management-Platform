from typing import Any

from backend.app.integrations.jira.client import JiraClient
from backend.app.integrations.jira.schemas import CreateEpicRequest
from backend.app.integrations.jira.schemas import CreateStoryRequest
from backend.app.integrations.jira.schemas import CreateTaskRequest
from backend.app.integrations.jira.schemas import AssignIssueRequest
from backend.app.integrations.jira.schemas import UpdateIssueRequest
from backend.app.integrations.jira.schemas import TransitionIssueRequest
from backend.app.integrations.jira.schemas import AddCommentRequest
from backend.app.integrations.jira.schemas import LinkIssuesRequest

class JiraService:
    """Business-level Jira operations built on top of JiraClient."""

    def __init__(self, client: JiraClient | None = None) -> None:
        self.client = client or JiraClient()

    
    async def get_project(self, project_key: str) -> dict[str, Any]:
        """Get Jira project details."""
        return await self.client.get(
            f"/rest/api/3/project/{project_key}"
        )

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Get a Jira issue by key."""
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}"
        )

    async def search_issues(
        self,
        jql: str,
        *,
        start_at: int = 0,
        max_results: int = 50,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search Jira issues using JQL."""
        params: dict[str, Any] = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
        }

        if fields:
            params["fields"] = ",".join(fields)

        return await self.client.get(
            "/rest/api/3/search/jql",
            params=params,
        )

    async def get_project_issues(
        self,
        project_key: str,
        *,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get issues belonging to a Jira project."""
        jql = f"project = {project_key} ORDER BY created DESC"

        return await self.search_issues(
            jql,
            start_at=start_at,
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
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}/comment",
            params={
                "startAt": start_at,
                "maxResults": max_results,
            },
        )
    

    async def get_issue_history(
        self,
        issue_key: str,
        *,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get issue changelog/history."""
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}/changelog",
            params={
                "startAt": start_at,
                "maxResults": max_results,
            },
        )

    async def get_issue_dependencies(
        self,
        issue_key: str,
    ) -> dict[str, Any]:
        """Get issue details including linked issues/dependencies."""
        return await self.client.get(
            f"/rest/api/3/issue/{issue_key}",
            params={"fields": "issuelinks"},
        )

    async def get_sprint(
        self,
        sprint_id: int,
    ) -> dict[str, Any]:
        """Get Jira sprint details."""
        return await self.client.get(
            f"/rest/agile/1.0/sprint/{sprint_id}"
        )

    async def get_sprint_issues(
        self,
        sprint_id: int,
        *,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Get issues belonging to a Jira sprint."""
        return await self.client.get(
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            params={
                "startAt": start_at,
                "maxResults": max_results,
            },
        )
    async def create_epic(
    self,
    request: CreateEpicRequest,
    ) -> dict[str, Any]:
        """Create a Jira epic."""
        payload: dict[str, Any] = {
            "fields": {
                "project": {
                    "key": request.project_key,
                },
                "summary": request.summary,
                "issuetype": {
                    "name": "Epic",
                },
            }
        }

        if request.description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": request.description,
                            }
                        ],
                    }
                ],
            }

        return await self.client.post(
            "/rest/api/3/issue",
            payload,
        )
    
    async def create_story(
        self,
        request: CreateStoryRequest,
    ) -> dict[str, Any]:
        """Create a Jira story."""
        payload: dict[str, Any] = {
            "fields": {
                "project": {
                    "key": request.project_key,
                },
                "summary": request.summary,
                "issuetype": {
                    "name": "Story",
                },
            }
        }

        if request.description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": request.description,
                            }
                        ],
                    }
                ],
            }

        if request.parent_key:
            payload["fields"]["parent"] = {
                "key": request.parent_key,
            }

        return await self.client.post(
            "/rest/api/3/issue",
            payload,
        )
    async def create_task(
        self,
        request: CreateTaskRequest,
    ) -> dict[str, Any]:
        """Create a Jira task."""
        payload: dict[str, Any] = {
            "fields": {
                "project": {
                    "key": request.project_key,
                },
                "summary": request.summary,
                "issuetype": {
                    "name": "Task",
                },
            }
        }

        if request.description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": request.description,
                            }
                        ],
                    }
                ],
            }

        if request.parent_key:
            payload["fields"]["parent"] = {
                "key": request.parent_key,
            }

        return await self.client.post(
            "/rest/api/3/issue",
            payload,
        )

    async def assign_issue(
        self,
        request: AssignIssueRequest,
    ) -> dict[str, Any]:
        """Assign a Jira issue to a user."""
        payload = {
            "accountId": request.account_id,
        }

        await self.client.put(
            f"/rest/api/3/issue/{request.issue_key}/assignee",
            payload,
        )

        return {
            "issue_key": request.issue_key,
            "account_id": request.account_id,
            "status": "assigned",
        }
    async def update_issue(
        self,
        request: UpdateIssueRequest,
    ) -> dict[str, Any]:
        """Update supported fields on a Jira issue."""
        fields: dict[str, Any] = {}

        if request.summary is not None:
            fields["summary"] = request.summary

        if request.description is not None:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": request.description,
                            }
                        ],
                    }
                ],
            }

        if request.priority_id is not None:
            fields["priority"] = {
                "id": request.priority_id,
            }

        if request.assignee_account_id is not None:
            fields["assignee"] = {
                "accountId": request.assignee_account_id,
            }

        if not fields:
            raise ValueError("At least one issue field must be provided for update")

        await self.client.put(
            f"/rest/api/3/issue/{request.issue_key}",
            {"fields": fields},
        )

        return {
            "issue_key": request.issue_key,
            "updated_fields": list(fields.keys()),
            "status": "updated",
        }

    async def transition_issue(
        self,
        request: TransitionIssueRequest,
    ) -> dict[str, Any]:
        """Transition a Jira issue to another workflow state."""
        payload = {
            "transition": {
                "id": request.transition_id,
            }
        }

        await self.client.post(
            f"/rest/api/3/issue/{request.issue_key}/transitions",
            payload,
        )

        return {
            "issue_key": request.issue_key,
            "transition_id": request.transition_id,
            "status": "transitioned",
        }

    async def add_comment(
        self,
        request: AddCommentRequest,
    ) -> dict[str, Any]:
        """Add a comment to a Jira issue."""
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": request.comment,
                            }
                        ],
                    }
                ],
            }
        }

        result = await self.client.post(
            f"/rest/api/3/issue/{request.issue_key}/comment",
            payload,
        )

        return {
            "issue_key": request.issue_key,
            "comment": request.comment,
            "jira_response": result,
            "status": "comment_added",
        }

    async def link_issues(
        self,
        request: LinkIssuesRequest,
    ) -> dict[str, Any]:
        """Create a link between two Jira issues."""
        payload = {
            "type": {
                "name": request.link_type,
            },
            "inwardIssue": {
                "key": request.issue_key,
            },
            "outwardIssue": {
                "key": request.linked_issue_key,
            },
        }

        await self.client.post(
            "/rest/api/3/issueLink",
            payload,
        )

        return {
            "issue_key": request.issue_key,
            "linked_issue_key": request.linked_issue_key,
            "link_type": request.link_type,
            "status": "linked",
        }