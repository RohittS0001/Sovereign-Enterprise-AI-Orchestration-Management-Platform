from typing import Any

from backend.app.integrations.github.service import GitHubService
from backend.app.tools.base import ToolResult
from backend.app.tools.github import schemas as s
from backend.app.tools.runner import run_tool


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


class GitHubTools:
    """MCP-facing tools for GitHub."""

    def __init__(self, service: GitHubService | None = None) -> None:
        self.service = service or GitHubService()

    async def list_pull_requests(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "list pull requests", payload, s.ListPullRequestsInput,
            lambda i: self.service.list_pull_requests(
                i.repository, state=i.state, base=i.base, limit=i.limit
            ),
        )  # fmt: skip

    async def get_pull_request(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "get pull request", payload, s.GetPullRequestInput,
            lambda i: self.service.get_pull_request(i.repository, i.number),
        )  # fmt: skip

    async def search_pull_requests(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "search pull requests", payload, s.SearchPullRequestsInput,
            lambda i: self.service.search_pull_requests(i.query, limit=i.limit),
        )  # fmt: skip

    async def list_commits(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "list commits", payload, s.ListCommitsInput,
            lambda i: self.service.list_commits(
                i.repository, branch=i.branch, since=_iso(i.since), until=_iso(i.until),
                author=i.author, limit=i.limit,
            ),
        )  # fmt: skip

    async def list_workflow_runs(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "list workflow runs", payload, s.ListWorkflowRunsInput,
            lambda i: self.service.list_workflow_runs(
                i.repository, branch=i.branch, status=i.status, limit=i.limit
            ),
        )  # fmt: skip

    async def get_commit_checks(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "get commit checks", payload, s.GetCommitChecksInput,
            lambda i: self.service.get_commit_checks(i.repository, i.ref),
        )  # fmt: skip

    async def add_pr_comment(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "GitHub", "add pull request comment", payload, s.AddPullRequestCommentInput,
            lambda i: self.service.add_pr_comment(i.repository, i.number, i.body),
        )  # fmt: skip
