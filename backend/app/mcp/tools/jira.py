
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

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
from backend.app.integrations.jira.service import JiraService
from backend.app.mcp.schemas.common import ToolResult, ToolStatus
from backend.app.mcp.schemas.jira import (
    AddCommentInput,
    AssignIssueInput,
    CreateEpicInput,
    CreateStoryInput,
    CreateTaskInput,
    GetIssueCommentsInput,
    GetIssueDependenciesInput,
    GetIssueHistoryInput,
    GetIssueInput,
    GetProjectInput,
    GetProjectIssuesInput,
    GetSprintInput,
    GetSprintIssuesInput,
    LinkIssuesInput,
    SearchIssuesInput,
    TransitionIssueInput,
    UpdateIssueInput,
)

InputModel = TypeVar("InputModel", bound=BaseModel)


class JiraTools:
    """MCP-facing tools for Jira read and write operations."""

    def __init__(self, service: JiraService | None = None) -> None:
        self.service = service or JiraService()

    async def _execute(
        self,
        operation: str,
        payload: dict[str, Any],
        input_model: type[InputModel],
        handler: Callable[[InputModel], Awaitable[Any]],
    ) -> ToolResult:
        """Validate input, invoke a Jira service method, and normalize errors."""
        try:
            inputs = input_model.model_validate(payload)
            data = await handler(inputs)

            return ToolResult(
                status=ToolStatus.SUCCESS,
                data=data,
            )
        except ValidationError as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Invalid Jira input for {operation}: {exc}",
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Failed to {operation}: {exc}",
            )

    # --------------------------------------------------
    # Read tools
    # --------------------------------------------------

    async def get_project(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve project details by project key."""
        return await self._execute(
            "get project",
            payload,
            GetProjectInput,
            lambda inputs: self.service.get_project(inputs.project_key),
        )

    async def get_issue(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve issue details by issue key."""
        return await self._execute(
            "get issue",
            payload,
            GetIssueInput,
            lambda inputs: self.service.get_issue(inputs.issue_key),
        )

    async def search_issues(self, payload: dict[str, Any]) -> ToolResult:
        """Search issues using Jira Query Language (JQL)."""
        return await self._execute(
            "search issues",
            payload,
            SearchIssuesInput,
            lambda inputs: self.service.search_issues(
                inputs.jql,
                start_at=inputs.start_at,
                max_results=inputs.max_results,
                fields=inputs.fields,
            ),
        )

    async def get_project_issues(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve issues belonging to a project."""
        return await self._execute(
            "get project issues",
            payload,
            GetProjectIssuesInput,
            lambda inputs: self.service.get_project_issues(
                inputs.project_key,
                start_at=inputs.start_at,
                max_results=inputs.max_results,
            ),
        )

    async def get_issue_comments(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve comments for an issue."""
        return await self._execute(
            "get issue comments",
            payload,
            GetIssueCommentsInput,
            lambda inputs: self.service.get_issue_comments(
                inputs.issue_key,
                start_at=inputs.start_at,
                max_results=inputs.max_results,
            ),
        )

    async def get_issue_history(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve an issue's change history."""
        return await self._execute(
            "get issue history",
            payload,
            GetIssueHistoryInput,
            lambda inputs: self.service.get_issue_history(
                inputs.issue_key,
                start_at=inputs.start_at,
                max_results=inputs.max_results,
            ),
        )

    async def get_issue_dependencies(
        self,
        payload: dict[str, Any],
    ) -> ToolResult:
        """Retrieve an issue and its linked issue information."""
        return await self._execute(
            "get issue dependencies",
            payload,
            GetIssueDependenciesInput,
            lambda inputs: self.service.get_issue_dependencies(inputs.issue_key),
        )

    async def get_sprint(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve sprint details by sprint ID."""
        return await self._execute(
            "get sprint",
            payload,
            GetSprintInput,
            lambda inputs: self.service.get_sprint(inputs.sprint_id),
        )

    async def get_sprint_issues(self, payload: dict[str, Any]) -> ToolResult:
        """Retrieve issues belonging to a sprint."""
        return await self._execute(
            "get sprint issues",
            payload,
            GetSprintIssuesInput,
            lambda inputs: self.service.get_sprint_issues(
                inputs.sprint_id,
                start_at=inputs.start_at,
                max_results=inputs.max_results,
            ),
        )

    # --------------------------------------------------
    # Create tools
    # --------------------------------------------------

    async def create_epic(self, payload: dict[str, Any]) -> ToolResult:
        """Create a Jira epic."""
        return await self._execute(
            "create epic",
            payload,
            CreateEpicInput,
            lambda inputs: self.service.create_epic(
                CreateEpicRequest(**inputs.model_dump())
            ),
        )

    async def create_story(self, payload: dict[str, Any]) -> ToolResult:
        """Create a Jira story."""
        return await self._execute(
            "create story",
            payload,
            CreateStoryInput,
            lambda inputs: self.service.create_story(
                CreateStoryRequest(**inputs.model_dump())
            ),
        )

    async def create_task(self, payload: dict[str, Any]) -> ToolResult:
        """Create a Jira task."""
        return await self._execute(
            "create task",
            payload,
            CreateTaskInput,
            lambda inputs: self.service.create_task(
                CreateTaskRequest(**inputs.model_dump())
            ),
        )

    # --------------------------------------------------
    # Additional write tools
    # --------------------------------------------------

    async def assign_issue(self, payload: dict[str, Any]) -> ToolResult:
        """Assign a Jira issue to a user."""
        return await self._execute(
            "assign issue",
            payload,
            AssignIssueInput,
            lambda inputs: self.service.assign_issue(
                AssignIssueRequest(**inputs.model_dump())
            ),
        )

    async def update_issue(self, payload: dict[str, Any]) -> ToolResult:
        """Update supported fields on a Jira issue."""
        return await self._execute(
            "update issue",
            payload,
            UpdateIssueInput,
            lambda inputs: self.service.update_issue(
                UpdateIssueRequest(**inputs.model_dump())
            ),
        )

    async def transition_issue(self, payload: dict[str, Any]) -> ToolResult:
        """Transition a Jira issue to another workflow state."""
        return await self._execute(
            "transition issue",
            payload,
            TransitionIssueInput,
            lambda inputs: self.service.transition_issue(
                TransitionIssueRequest(**inputs.model_dump())
            ),
        )

    async def add_comment(self, payload: dict[str, Any]) -> ToolResult:
        """Add a comment to a Jira issue."""
        return await self._execute(
            "add comment",
            payload,
            AddCommentInput,
            lambda inputs: self.service.add_comment(
                AddCommentRequest(**inputs.model_dump())
            ),
        )

    async def link_issues(self, payload: dict[str, Any]) -> ToolResult:
        """Create a link between two Jira issues."""
        return await self._execute(
            "link issues",
            payload,
            LinkIssuesInput,
            lambda inputs: self.service.link_issues(
                LinkIssuesRequest(**inputs.model_dump())
            ),
        )
