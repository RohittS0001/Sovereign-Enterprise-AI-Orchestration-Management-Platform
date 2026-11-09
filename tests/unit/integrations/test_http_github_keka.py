"""Shared HTTP base behaviour, GitHub and Keka clients/services (over httpx.MockTransport)."""

import json
from datetime import date

import httpx
import pytest

from backend.app.core.exceptions.exceptions import ConfigurationError, IntegrationError
from backend.app.integrations.common.http import HttpIntegrationClient
from backend.app.integrations.github.client import GitHubClient
from backend.app.integrations.github.service import GitHubService, validate_repository
from backend.app.integrations.keka.client import KekaClient
from backend.app.integrations.keka.service import KekaService
from backend.app.mcp.schemas.common import ToolStatus
from backend.app.mcp.tools.github import GitHubTools
from backend.app.mcp.tools.keka import KekaTools


class Recorder:
    """MockTransport handler that replays queued responses and records requests."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def base_client(handler, sleeps: list[float]) -> HttpIntegrationClient:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return HttpIntegrationClient(
        base_url="https://api.example",
        timeout=5,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )


# ------------------------------------------------------------------ base client


async def test_get_waits_on_retry_after_then_succeeds() -> None:
    sleeps: list[float] = []
    handler = Recorder(
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"ok": True}),
    )

    result = await base_client(handler, sleeps).get("/x")

    assert result == {"ok": True}
    assert sleeps == [2.0]


async def test_github_style_403_rate_limit_is_recognized() -> None:
    sleeps: list[float] = []
    handler = Recorder(
        httpx.Response(403, headers={"x-ratelimit-remaining": "0", "retry-after": "1"}),
        httpx.Response(200, json=[]),
    )

    assert await base_client(handler, sleeps).get("/x") == []
    assert sleeps == [1.0]


async def test_long_waits_and_writes_fail_fast_as_retryable() -> None:
    sleeps: list[float] = []
    client = base_client(
        Recorder(
            httpx.Response(429, headers={"Retry-After": "3600"}),
            httpx.Response(429, headers={"Retry-After": "1"}),
        ),
        sleeps,
    )

    with pytest.raises(IntegrationError) as too_long:
        await client.get("/x")
    with pytest.raises(IntegrationError) as write:
        await client.post("/x", {})

    assert too_long.value.upstream_status == 429
    assert write.value.upstream_status == 429
    assert sleeps == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"message": "Bad credentials"}, "Bad credentials"),  # GitHub
        (
            {"message": "Validation Failed", "errors": [{"message": "title missing"}]},
            "title missing",
        ),
        (
            {"succeeded": False, "message": "Invalid", "errors": ["from is required"]},
            "from is required",
        ),
        ({"error": "invalid_client"}, "invalid_client"),  # OAuth
    ],
)
async def test_error_formats_are_parsed(body, expected) -> None:
    client = base_client(Recorder(httpx.Response(400, json=body)), [])

    with pytest.raises(IntegrationError, match=expected):
        await client.get("/x")


# ------------------------------------------------------------------ GitHub


def github_service(handler, allowed=None) -> GitHubService:
    client = GitHubClient(
        token="ghs_test", api_url="https://gh.example", transport=httpx.MockTransport(handler)
    )
    return GitHubService(client=client, allowed_repos=allowed or [])


def test_github_requires_token(monkeypatch) -> None:
    from pydantic import SecretStr

    from backend.app.integrations.github import client as module

    monkeypatch.setattr(module.settings, "github_token", SecretStr(""))
    with pytest.raises(ConfigurationError, match="GITHUB_TOKEN"):
        GitHubClient()


@pytest.mark.parametrize(
    "repo", ["acme", "acme/../secrets", "acme/..", "-bad/repo", "a/b/c", "acme/re po"]
)
def test_repository_names_are_validated(repo) -> None:
    with pytest.raises(ValueError):
        validate_repository(repo, [])


def test_allow_list_is_enforced_case_insensitively() -> None:
    assert validate_repository("ACME/Payments", ["acme/payments"]) == ("ACME", "Payments")
    with pytest.raises(ValueError, match="GITHUB_ALLOWED_REPOS"):
        validate_repository("acme/other", ["acme/payments"])


async def test_list_pull_requests_is_trimmed_and_authenticated() -> None:
    raw_pr = {
        "number": 7, "title": "PHX-7 fix", "state": "open", "draft": False,
        "user": {"login": "arjun", "avatar_url": "x"}, "head": {"ref": "fix", "repo": {"huge": "object"}},
        "base": {"ref": "main"}, "requested_reviewers": [{"login": "priya"}], "labels": [{"name": "bug"}],
        "html_url": "https://gh/pr/7", "merged_at": None,
    }  # fmt: skip
    handler = Recorder(httpx.Response(200, json=[raw_pr]))

    result = await github_service(handler).list_pull_requests("acme/payments", limit=5)

    request = handler.requests[0]
    assert request.url.path == "/repos/acme/payments/pulls"
    assert request.url.params["per_page"] == "5"
    assert request.headers["Authorization"] == "Bearer ghs_test"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    pr = result["pull_requests"][0]
    assert pr["author"] == "arjun" and pr["requested_reviewers"] == ["priya"]
    assert "repo" not in json.dumps(pr)  # nested repository objects dropped


async def test_get_pull_request_includes_reviews_and_merged_state() -> None:
    handler = Recorder(
        httpx.Response(
            200, json={"number": 9, "state": "closed", "merged_at": "2026-11-01T00:00:00Z"}
        ),
        httpx.Response(
            200, json=[{"user": {"login": "meera"}, "state": "APPROVED", "submitted_at": "t"}]
        ),
    )

    result = await github_service(handler).get_pull_request("acme/payments", 9)

    assert result["pull_request"]["state"] == "merged"
    assert result["reviews"] == [{"reviewer": "meera", "state": "APPROVED", "submitted_at": "t"}]
    assert handler.requests[1].url.path == "/repos/acme/payments/pulls/9/reviews"


async def test_search_is_confined_to_allowed_repos() -> None:
    handler = Recorder(httpx.Response(200, json={"total_count": 0, "items": []}))

    await github_service(handler, ["acme/payments"]).search_pull_requests(
        "PHX-7 repo:evil/secret org:evil"
    )

    query = handler.requests[0].url.params["q"]
    assert "evil" not in query
    assert query.startswith("is:pr PHX-7")
    assert query.endswith("repo:acme/payments")


async def test_workflow_runs_and_checks_are_summarized() -> None:
    handler = Recorder(
        httpx.Response(200, json={"total_count": 2, "workflow_runs": [
            {"id": 1, "name": "CI", "status": "completed", "conclusion": "failure", "head_sha": "abcdef123"},
            {"id": 2, "name": "CI", "status": "in_progress", "conclusion": None, "head_sha": "123456789"},
        ]}),
        httpx.Response(200, json={"check_runs": [
            {"name": "tests", "status": "completed", "conclusion": "failure"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ]}),
    )  # fmt: skip
    service = github_service(handler)

    runs = await service.list_workflow_runs("acme/payments", branch="main")
    checks = await service.get_commit_checks("acme/payments", "main")

    assert runs["conclusions"] == {"failure": 1, "in_progress": 1}
    assert runs["workflow_runs"][0]["sha"] == "abcdef1"
    assert checks["failing"] == ["tests"]
    assert handler.requests[0].url.params["branch"] == "main"


async def test_github_tool_errors_are_contained() -> None:
    handler = Recorder(httpx.Response(502, json={"message": "Bad gateway"}))
    tools = GitHubTools(service=github_service(handler))

    bad_repo = await tools.list_pull_requests({"repository": "../etc"})
    upstream = await tools.list_pull_requests({"repository": "acme/payments"})

    assert bad_repo.status == ToolStatus.ERROR and "Invalid GitHub input" in bad_repo.error
    assert upstream.status == ToolStatus.ERROR and upstream.retryable is True


async def test_add_pr_comment_posts_to_issue_comments() -> None:
    handler = Recorder(httpx.Response(201, json={"id": 55, "html_url": "u"}))

    result = await github_service(handler).add_pr_comment("acme/payments", 103, "LGTM after fix")

    assert handler.requests[0].method == "POST"
    assert handler.requests[0].url.path == "/repos/acme/payments/issues/103/comments"
    assert json.loads(handler.requests[0].content) == {"body": "LGTM after fix"}
    assert result["comment_id"] == 55


# ------------------------------------------------------------------ Keka


def keka_client(handler) -> KekaClient:
    return KekaClient(
        base_url="https://acme.keka.com/api/v1",
        token_url="https://login.keka.com/connect/token",
        client_id="cid",
        client_secret="secret",
        api_key="key",
        transport=httpx.MockTransport(handler),
    )


def token_response(token: str = "tok-1") -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": token, "expires_in": 86400, "token_type": "Bearer"}
    )


def test_keka_reports_missing_settings() -> None:
    with pytest.raises(ConfigurationError, match="KEKA_API_KEY"):
        KekaClient(base_url="https://x", client_id="c", client_secret="s", api_key="")


async def test_keka_fetches_token_once_and_sends_bearer() -> None:
    handler = Recorder(
        token_response(),
        httpx.Response(200, json={"succeeded": True, "data": [], "totalRecords": 0}),
        httpx.Response(200, json={"succeeded": True, "data": [], "totalRecords": 0}),
    )
    client = keka_client(handler)

    await client.get("/hris/employees")
    await client.get("/hris/employees")

    token_request, first, second = handler.requests
    assert str(token_request.url) == "https://login.keka.com/connect/token"
    form = dict(pair.split("=") for pair in token_request.content.decode().split("&"))
    assert form == {
        "grant_type": "kekaapi", "scope": "kekaapi", "client_id": "cid",
        "client_secret": "secret", "api_key": "key",
    }  # fmt: skip
    assert first.headers["Authorization"] == second.headers["Authorization"] == "Bearer tok-1"
    assert "Mozilla" in first.headers["User-Agent"]


async def test_keka_refreshes_token_after_401() -> None:
    handler = Recorder(
        token_response("old"),
        httpx.Response(401),
        token_response("new"),
        httpx.Response(200, json={"data": []}),
    )

    await keka_client(handler).get("/time/leaverequests")

    assert handler.requests[-1].headers["Authorization"] == "Bearer new"


async def test_keka_token_failure_is_an_integration_error() -> None:
    handler = Recorder(httpx.Response(400, json={"error": "invalid_client"}))

    with pytest.raises(IntegrationError, match="invalid_client"):
        await keka_client(handler).get("/hris/employees")


async def test_employees_are_minimized() -> None:
    employee = {
        "id": "e-1", "displayName": "Arjun M", "email": "arjun@acme.example",
        "jobTitle": {"identifier": "j1", "title": "Backend Engineer"},
        "reportsTo": {"id": "e-0", "displayName": "Priya N"},
        "dateOfBirth": "1990-01-01", "personalEmail": "a@gmail.example",
        "mobilePhone": "+91 99999 99999", "bankDetails": {"account": "123"},
    }  # fmt: skip
    handler = Recorder(
        token_response(), httpx.Response(200, json={"data": [employee], "totalRecords": 1})
    )

    result = await KekaService(keka_client(handler)).search_employees("arj", limit=10)

    request = handler.requests[1]
    assert request.url.params["searchKey"] == "arj"
    assert request.url.params["employmentStatus"] == "Working"
    [summary] = result["employees"]
    assert summary["job_title"] == "Backend Engineer"
    assert summary["reports_to"] == {"id": "e-0", "name": "Priya N"}
    serialized = json.dumps(summary)
    for private in ("1990-01-01", "gmail", "99999", "bank"):
        assert private not in serialized


async def test_leave_requests_validate_range_and_ids() -> None:
    handler = Recorder(token_response(), httpx.Response(200, json={"data": [
        {"id": "l1", "employeeIdentifier": "e-1", "fromDate": "2026-11-10", "toDate": "2026-11-12",
         "status": 1, "selection": [{"leaveTypeName": "Earned Leave", "count": 3}]},
    ], "totalRecords": 1}))  # fmt: skip
    service = KekaService(keka_client(handler))

    result = await service.get_leave_requests(date(2026, 11, 1), date(2026, 11, 30), ["e-1"])

    params = handler.requests[1].url.params
    assert (params["from"], params["to"], params["employeeIds"]) == (
        "2026-11-01",
        "2026-11-30",
        "e-1",
    )
    assert result["leave_requests"][0]["leave_types"] == [{"type": "Earned Leave", "count": 3}]

    with pytest.raises(ValueError, match="90 days"):
        await service.get_leave_requests(date(2026, 1, 1), date(2026, 6, 1))
    with pytest.raises(ValueError, match="on or before"):
        await service.get_leave_requests(date(2026, 2, 1), date(2026, 1, 1))
    with pytest.raises(ValueError, match="Invalid Keka employee id"):
        await service.get_leave_requests(date(2026, 1, 1), date(2026, 1, 5), ["e-1,e-2"])


async def test_keka_tool_rejects_short_search() -> None:
    result = await KekaTools(service=KekaService(keka_client(Recorder()))).search_employees(
        {"search": "ab"}
    )

    assert result.status == ToolStatus.ERROR
