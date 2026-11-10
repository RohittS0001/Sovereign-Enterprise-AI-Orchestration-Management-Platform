"""Tracker connectors against mocked vendor APIs: request shape, normalization, validation."""

import base64

import pytest

from backend.app.integrations.connectors.trackers import (
    AsanaService,
    AzureDevOpsService,
    BitbucketService,
    ClickUpService,
    GitLabService,
    LinearService,
)
from tests.support.connectors import MockApi


async def test_linear_uses_graphql_variables_and_maps_state_types() -> None:
    node = {"identifier": "ENG-7", "title": "Fix login", "url": "https://linear.app/x/ENG-7",
            "updatedAt": "2026-11-08T10:00:00Z", "dueDate": "2026-11-20", "estimate": 3,
            "priorityLabel": "High", "state": {"name": "In Review", "type": "started"},
            "assignee": {"name": "Sara"}, "team": {"key": "ENG"}}  # fmt: skip
    api = MockApi({"POST /graphql": {"data": {"issues": {"nodes": [node]}}}})
    svc = LinearService(api.factory("Linear", "https://api.linear.app",
                                    headers={"Authorization": "lin_key"}), team_keys=["ENG"])  # fmt: skip

    [item] = await svc.search_work_items(query='x"}){evil', status="in_progress")

    body = api.json_body()
    assert body["variables"]["f"] == {
        "team": {"key": {"in": ["ENG"]}},
        "state": {"type": {"in": ["started"]}},
        "title": {"containsIgnoreCase": 'x"}){evil'},  # passed as a variable, never spliced
    }
    assert api.last.headers["authorization"] == "lin_key"
    assert (item.key, item.category, item.points, item.assignee) == (
        "ENG-7",
        "in_progress",
        3,
        "Sara",
    )
    with pytest.raises(ValueError, match="allowed"):
        await svc.search_work_items(project="OPS")


async def test_linear_graphql_errors_surface() -> None:
    api = MockApi({"POST /graphql": {"errors": [{"message": "Authentication required"}]}})
    svc = LinearService(api.factory("Linear", "https://api.linear.app"))
    with pytest.raises(ValueError, match="Authentication required"):
        await svc.health_check()


async def test_gitlab_encodes_projects_and_normalizes_issues_and_mrs() -> None:
    issue = {"iid": 12, "title": "Crash", "state": "opened", "labels": ["Doing", "priority::high"],
             "assignees": [{"name": "Meera"}], "due_date": "2026-11-20",
             "updated_at": "2026-11-09T00:00:00Z", "web_url": "https://gitlab.com/a/b/-/issues/12",
             "references": {"full": "acme/app#12"}}  # fmt: skip
    api = MockApi({
        "GET /api/v4/projects/acme%2Fapp/issues": [issue],
        "GET /api/v4/projects/acme%2Fapp/issues/12": issue,
        "GET /api/v4/projects/acme%2Fapp/merge_requests": [
            {"iid": 3, "title": "Fix", "state": "opened", "author": {"name": "Meera"},
             "updated_at": "2026-11-09", "web_url": "https://gitlab.com/a/b/-/merge_requests/3"}],
    })  # fmt: skip
    svc = GitLabService(api.factory("GitLab", "https://gitlab.com/api/v4"), projects=["acme/app"])

    [item] = await svc.search_work_items(project="acme/app", query="crash")
    assert api.last.url.params["search"] == "crash" and api.last.url.params["state"] == "all"
    assert (item.key, item.category, item.priority) == (
        "acme/app#12",
        "in_progress",
        "priority::high",
    )
    assert (await svc.get_work_item("acme/app#12")).title == "Crash"
    [mr] = await svc.list_pull_requests("acme/app", state="open")
    assert api.last.url.params["state"] == "opened" and mr.number == 3
    with pytest.raises(ValueError):
        await svc.search_work_items(project="other/repo")
    with pytest.raises(ValueError):
        await svc.get_work_item("acme/app#../../users")


async def test_azure_devops_wiql_escapes_quotes_then_fetches_the_batch() -> None:
    api = MockApi({
        "POST /acme/Payments/_apis/wit/wiql": {"workItems": [{"id": 7}, {"id": 9}]},
        "GET /acme/_apis/wit/workitems": {"value": [
            {"id": 7, "fields": {"System.Title": "Export", "System.State": "Active",
                                 "System.AssignedTo": {"displayName": "Arjun"},
                                 "Microsoft.VSTS.Scheduling.StoryPoints": 5,
                                 "System.TeamProject": "Payments"}},
        ]},
    })  # fmt: skip
    pat = "secret-pat"  # noqa: S105
    svc = AzureDevOpsService(api.factory("Azure DevOps", "https://dev.azure.com/acme",
                                         auth=("", pat)), projects=["Payments"])  # fmt: skip

    [item] = await svc.search_work_items(query="O'Brien export", status="in_progress")

    wiql = api.json_body(0)["query"]
    assert "CONTAINS 'O''Brien export'" in wiql and "[System.State] IN ('Active'" in wiql
    assert api.requests[0].url.params["api-version"] == "7.1"
    assert api.last.url.params["ids"] == "7,9"
    assert (
        api.last.headers["authorization"]
        == "Basic " + base64.b64encode(f":{pat}".encode()).decode()
    )
    assert (item.key, item.category, item.assignee, item.points) == ("7", "in_progress", "Arjun", 5)
    with pytest.raises(ValueError):
        await svc.get_work_item("7 OR 1=1")


async def test_asana_and_clickup_filter_client_side() -> None:
    asana = MockApi({"GET /api/1.0/projects/111/tasks": {"data": [
        {"gid": "1", "name": "Write runbook", "completed": False,
         "memberships": [{"section": {"name": "In Progress"}}]},
        {"gid": "2", "name": "Old task", "completed": True},
    ]}})  # fmt: skip
    a = AsanaService(asana.factory("Asana", "https://app.asana.com/api/1.0"), projects=["111"])
    assert [i.key for i in await a.search_work_items(status="in_progress")] == ["1"]
    assert asana.last.url.params["opt_fields"].startswith("name,completed")

    clickup = MockApi({"GET /api/v2/list/900/task": {"tasks": [
        {"id": "abc", "custom_id": "CU-5", "name": "Ship it", "status": {"status": "complete", "type": "closed"},
         "date_updated": "1762646400000", "assignees": [{"username": "dev"}]},
    ]}})  # fmt: skip
    c = ClickUpService(clickup.factory("ClickUp", "https://api.clickup.com/api/v2"), lists=["900"])
    [task] = await c.search_work_items(status="done")
    assert (task.key, task.category, task.assignee) == ("CU-5", "done", "dev")
    assert task.updated is not None  # epoch milliseconds parsed
    with pytest.raises(ValueError, match="required"):
        await ClickUpService(clickup.factory("ClickUp", "https://x")).search_work_items()


async def test_bitbucket_builds_bbql_without_injected_quotes() -> None:
    api = MockApi({
        "GET /2.0/repositories/acme/web/issues": {"values": [
            {"id": 4, "title": "Broken link", "state": "new", "updated_on": "2026-11-09"}]},
        "GET /2.0/repositories/acme/web/pullrequests": {"values": [
            {"id": 21, "title": "Fix link", "state": "OPEN", "author": {"display_name": "Sara"}}]},
    })  # fmt: skip
    svc = BitbucketService(api.factory("Bitbucket", "https://api.bitbucket.org/2.0"),
                           workspace="acme", repos=["web"])  # fmt: skip

    [issue] = await svc.search_work_items(query='link" OR state != "x', status="todo")
    assert api.last.url.params["q"] == ('title ~ "link OR state != x" AND '
                                        '(state = "new" OR state = "open")')  # fmt: skip
    assert issue.key == "web#4"
    [pr] = await svc.list_pull_requests(state="open")
    assert api.last.url.params["state"] == "OPEN" and pr.state == "open"
    with pytest.raises(ValueError):
        await svc.list_pull_requests(repository="secret-repo")
