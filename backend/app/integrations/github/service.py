"""GitHub operations for delivery insight: pull requests, reviews, commits and CI.

Results are trimmed to the fields that matter for project management, which keeps
model prompts small (raw GitHub payloads embed whole repository objects).
"""

import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from backend.app.core.config.settings import settings
from backend.app.integrations.github.client import GitHubClient

_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_SCOPE_QUALIFIER = re.compile(r"\b(repo|org|user):\S+", re.IGNORECASE)


def validate_repository(repository: str, allowed: list[str]) -> tuple[str, str]:
    repository = repository.strip()
    owner, _, name = repository.partition("/")
    if not _REPO.match(repository) or name in {".", ".."}:
        raise ValueError(f"Invalid repository {repository!r}; expected 'owner/name'")
    if allowed and repository.lower() not in {r.lower() for r in allowed}:
        raise ValueError(f"Repository {repository!r} is not in GITHUB_ALLOWED_REPOS")
    return owner, name


def validate_ref(ref: str) -> str:
    if not _REF.match(ref) or ".." in ref:
        raise ValueError(f"Invalid git ref {ref!r}")
    return ref


def _login(user: dict | None) -> str | None:
    return user.get("login") if user else None


def summarize_pull(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": "merged" if pr.get("merged_at") else pr.get("state"),
        "draft": pr.get("draft"),
        "author": _login(pr.get("user")),
        "head": (pr.get("head") or {}).get("ref"),
        "base": (pr.get("base") or {}).get("ref"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "merged_at": pr.get("merged_at"),
        "closed_at": pr.get("closed_at"),
        "requested_reviewers": [_login(u) for u in pr.get("requested_reviewers") or []],
        "labels": [label.get("name") for label in pr.get("labels") or []],
        "comments": pr.get("comments"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
        "mergeable_state": pr.get("mergeable_state"),
        "url": pr.get("html_url"),
    }


def summarize_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "reviewer": _login(review.get("user")),
        "state": review.get("state"),
        "submitted_at": review.get("submitted_at"),
    }


def summarize_commit(commit: dict[str, Any]) -> dict[str, Any]:
    detail = commit.get("commit") or {}
    message = (detail.get("message") or "").splitlines()
    return {
        "sha": (commit.get("sha") or "")[:7],
        "message": message[0] if message else "",
        "author": _login(commit.get("author")) or (detail.get("author") or {}).get("name"),
        "date": (detail.get("author") or {}).get("date"),
    }


def summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "workflow": run.get("name"),
        "run_number": run.get("run_number"),
        "event": run.get("event"),
        "branch": run.get("head_branch"),
        "sha": (run.get("head_sha") or "")[:7],
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "url": run.get("html_url"),
    }


class GitHubService:
    def __init__(
        self,
        client: GitHubClient | None = None,
        allowed_repos: list[str] | None = None,
        *,
        client_factory: Callable[[], GitHubClient] = GitHubClient,
    ) -> None:
        self._client = client
        self._client_factory = client_factory
        self.allowed_repos = (
            settings.github_allowed_repos if allowed_repos is None else allowed_repos
        )

    @property
    def client(self) -> GitHubClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def health_check(self) -> dict:
        return await self.client.health_check()

    def _repo_path(self, repository: str) -> str:
        owner, name = validate_repository(repository, self.allowed_repos)
        return f"/repos/{owner}/{name}"

    async def list_pull_requests(
        self, repository: str, *, state: str = "open", base: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "state": state,
            "per_page": limit,
            "sort": "updated",
            "direction": "desc",
        }
        if base:
            params["base"] = validate_ref(base)
        pulls = await self.client.get(f"{self._repo_path(repository)}/pulls", params=params)
        return {"repository": repository, "pull_requests": [summarize_pull(p) for p in pulls]}

    async def get_pull_request(self, repository: str, number: int) -> dict[str, Any]:
        path = f"{self._repo_path(repository)}/pulls/{int(number)}"
        pr = await self.client.get(path)
        reviews = await self.client.get(f"{path}/reviews", params={"per_page": 100})
        return {
            "repository": repository,
            "pull_request": summarize_pull(pr),
            "reviews": [summarize_review(r) for r in reviews],
        }

    async def search_pull_requests(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        query = " ".join(query.split())
        if self.allowed_repos:
            # Keep searches inside the allow-list: drop scope qualifiers, add allowed repos.
            query = _SCOPE_QUALIFIER.sub("", query).strip()
            query = f"{query} " + " ".join(f"repo:{r}" for r in self.allowed_repos)
        result = await self.client.get(
            "/search/issues",
            params={"q": f"is:pr {query}".strip(), "per_page": limit, "sort": "updated"},
        )
        items = result.get("items", [])
        return {
            "total_count": result.get("total_count", len(items)),
            "pull_requests": [
                {
                    **summarize_pull(item),
                    "repository": "/".join((item.get("repository_url") or "").rsplit("/", 2)[-2:]),
                }
                for item in items
            ],
        }

    async def list_commits(
        self,
        repository: str,
        *,
        branch: str | None = None,
        since: str | None = None,
        until: str | None = None,
        author: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": limit}
        if branch:
            params["sha"] = validate_ref(branch)
        for key, value in (("since", since), ("until", until), ("author", author)):
            if value:
                params[key] = value
        commits = await self.client.get(f"{self._repo_path(repository)}/commits", params=params)
        return {"repository": repository, "commits": [summarize_commit(c) for c in commits]}

    async def list_workflow_runs(
        self,
        repository: str,
        *,
        branch: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": limit}
        if branch:
            params["branch"] = validate_ref(branch)
        if status:
            params["status"] = status
        result = await self.client.get(f"{self._repo_path(repository)}/actions/runs", params=params)
        runs = [summarize_run(r) for r in result.get("workflow_runs", [])]
        return {
            "repository": repository,
            "total_count": result.get("total_count", len(runs)),
            "conclusions": dict(Counter(r["conclusion"] or r["status"] for r in runs)),
            "workflow_runs": runs,
        }

    async def get_commit_checks(self, repository: str, ref: str) -> dict[str, Any]:
        result = await self.client.get(
            f"{self._repo_path(repository)}/commits/{validate_ref(ref)}/check-runs",
            params={"per_page": 100},
        )
        checks = [
            {
                "name": c.get("name"),
                "status": c.get("status"),
                "conclusion": c.get("conclusion"),
                "completed_at": c.get("completed_at"),
            }
            for c in result.get("check_runs", [])
        ]
        return {
            "repository": repository,
            "ref": ref,
            "summary": dict(Counter(c["conclusion"] or c["status"] for c in checks)),
            "failing": [c["name"] for c in checks if c["conclusion"] in {"failure", "timed_out"}],
            "checks": checks,
        }

    async def add_pr_comment(self, repository: str, number: int, body: str) -> dict[str, Any]:
        comment = await self.client.post(
            f"{self._repo_path(repository)}/issues/{int(number)}/comments", {"body": body}
        )
        return {
            "repository": repository,
            "number": number,
            "comment_id": comment.get("id"),
            "url": comment.get("html_url"),
            "status": "comment_added",
        }
