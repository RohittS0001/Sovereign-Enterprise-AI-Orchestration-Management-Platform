import pytest

from backend.app.core.security.permissions import Role
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, build_registry, fake_jira_service, models, plan


@pytest.fixture
def jira():
    return fake_jira_service()


@pytest.fixture
def agent_client_factory(test_settings, database, jira):
    def factory(planner_replies, answers, settings=None):
        settings = settings or test_settings
        agent_models, _, _ = models(planner_replies, answers)
        app = create_app(
            settings,
            database,
            agent_models=agent_models,
            tool_registry=build_registry(settings, jira_service=jira),
        )
        return serve(app)

    return factory


async def test_agent_query_requires_authentication(client) -> None:
    response = await client.post("/api/v1/agent/query", json={"query": "status?"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_auditor_cannot_use_the_agent(client, make_user, auth_headers) -> None:
    auditor = await make_user(role=Role.AUDITOR)

    response = await client.post(
        "/api/v1/agent/query", json={"query": "hi"}, headers=auth_headers(auditor)
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"missing_permissions": ["agent:query"]}


async def test_agent_unavailable_without_llm(client, make_user, auth_headers) -> None:
    response = await client.post(
        "/api/v1/agent/query", json={"query": "hi"}, headers=auth_headers(await make_user())
    )

    assert response.status_code == 503
    assert "LLM1_MODEL_NAME" in response.json()["error"]["message"]


async def test_agent_query_end_to_end(agent_client_factory, make_user, auth_headers) -> None:
    headers = auth_headers(await make_user(role=Role.MEMBER))

    async with agent_client_factory(
        [plan(("jira.get_issue", {"issue_key": "PHX-7"})), DONE],
        ["PHX-7 is blocked [S1]."],
    ) as client:
        response = await client.post(
            "/api/v1/agent/query", json={"query": "  What's up with PHX-7? "}, headers=headers
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["query"] == "What's up with PHX-7?"
    assert body["answer"] == "PHX-7 is blocked [S1]."
    assert body["citations"] == ["S1"]
    assert body["steps"] == [
        {
            "id": "S1",
            "tool": "jira.get_issue",
            "purpose": "call jira.get_issue",
            "status": "success",
            "error": None,
            "approval_id": None,
            "suspicious": False,
        }
    ]
    assert body["usage"]["prompt_tokens"] > 0


async def test_agent_query_validation(agent_client_factory, make_user, auth_headers) -> None:
    headers = auth_headers(await make_user())

    async with agent_client_factory([], []) as client:
        missing = await client.post("/api/v1/agent/query", json={}, headers=headers)
        blank = await client.post("/api/v1/agent/query", json={"query": "   "}, headers=headers)
        long = await client.post("/api/v1/agent/query", json={"query": "x" * 4001}, headers=headers)

    assert missing.status_code == 422
    assert missing.json()["error"]["details"][0]["loc"] == ["body", "query"]
    assert blank.status_code == 422
    assert long.status_code == 422


async def test_agent_query_is_rate_limited_per_user(
    agent_client_factory, make_user, auth_headers, test_settings
) -> None:
    settings = test_settings.model_copy(update={"agent_rate_limit_per_minute": 2})
    user, other = await make_user(), await make_user()

    async with agent_client_factory([DONE] * 3, ["a"] * 3, settings) as client:
        codes = [
            (
                await client.post(
                    "/api/v1/agent/query", json={"query": "q"}, headers=auth_headers(user)
                )
            ).status_code
            for _ in range(3)
        ]
        other_code = (
            await client.post(
                "/api/v1/agent/query", json={"query": "q"}, headers=auth_headers(other)
            )
        ).status_code

    assert codes == [200, 200, 429]
    assert other_code == 200


async def test_runs_are_listed_and_private(agent_client_factory, make_user, auth_headers) -> None:
    owner, stranger = await make_user(), await make_user()

    async with agent_client_factory([DONE, DONE], ["one", "two"]) as client:
        first = await client.post(
            "/api/v1/agent/query", json={"query": "first"}, headers=auth_headers(owner)
        )
        await client.post(
            "/api/v1/agent/query", json={"query": "second"}, headers=auth_headers(owner)
        )
        run_id = first.json()["run_id"]

        listing = (await client.get("/api/v1/agent/runs", headers=auth_headers(owner))).json()
        mine = await client.get(f"/api/v1/agent/runs/{run_id}", headers=auth_headers(owner))
        theirs = await client.get(f"/api/v1/agent/runs/{run_id}", headers=auth_headers(stranger))
        empty = (await client.get("/api/v1/agent/runs", headers=auth_headers(stranger))).json()

    assert listing["total"] == 2
    assert [r["query"] for r in listing["items"]] == ["second", "first"]
    assert mine.json()["answer"] == "one"
    assert theirs.status_code == 404
    assert empty["total"] == 0


async def test_tools_endpoint_reflects_role_and_approval(
    agent_client_factory, make_user, auth_headers
) -> None:
    async with agent_client_factory([], []) as client:
        member_tools = (
            await client.get("/api/v1/agent/tools", headers=auth_headers(await make_user()))
        ).json()
        lead_tools = (
            await client.get(
                "/api/v1/agent/tools",
                headers=auth_headers(await make_user(role=Role.TEAM_LEAD)),
            )
        ).json()

    assert all(t["operation"] == "read" for t in member_tools)
    create = next(t for t in lead_tools if t["name"] == "jira.create_task")
    assert create["requires_approval"] is True
    assert create["input_schema"]["properties"]["project_key"]
