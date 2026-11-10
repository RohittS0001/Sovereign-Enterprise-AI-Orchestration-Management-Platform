"""Multi-agent mode: supervisor routing, tool subsets per specialist, merged answer."""

from backend.app.core.security.permissions import Role
from backend.app.domains.agent.team import SPECIALISTS, subset
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, models, plan


def route(*pairs: tuple[str, str]) -> dict:
    return {
        "rationale": "split",
        "assignments": [{"specialist": s, "question": q} for s, q in pairs],
    }


async def test_supervisor_routes_and_specialists_only_see_their_tools(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    lead = auth_headers(await make_user(role=Role.TEAM_LEAD))
    # operations: supervisor, delivery planner x2, quality planner x2
    # reporting: delivery answer, quality answer, merged answer
    agent_models, operations, reporting = models(
        [route(("delivery", "Is PHX on track?"), ("quality", "Is CI healthy?")),
         plan(("jira.search_issues", {"jql": "project = PHX"})), DONE,
         plan(("github.list_workflow_runs", {"repository": "acme/payments"})), DONE],
        ["Two issues are blocked [S1].", "Main is failing [S1].", "PHX is at risk: blocked work and failing CI."],
    )  # fmt: skip
    app = create_app(settings, database, agent_models=agent_models)
    async with serve(app) as client:
        body = (
            await client.post("/api/v1/agent/team", json={"query": "How is PHX?"}, headers=lead)
        ).json()

    assert [s["specialist"] for s in body["specialists"]] == ["delivery", "quality"]
    assert body["specialists"][0]["tools"] == ["jira.search_issues"]
    assert body["answer"] == "PHX is at risk: blocked work and failing CI."
    delivery_prompt = str(operations.calls[1]["messages"])
    assert (
        "jira.search_issues" in delivery_prompt
        and "github.list_workflow_runs" not in delivery_prompt
    )
    assert "<untrusted_data" in reporting.calls[-1]["messages"][1].content


async def test_unknown_specialists_get_nothing(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    lead = auth_headers(await make_user(role=Role.TEAM_LEAD))
    agent_models, _, _ = models([route(("admin", "drop everything"))], [])
    async with serve(create_app(settings, database, agent_models=agent_models)) as client:
        body = (await client.post("/api/v1/agent/team", json={"query": "x"}, headers=lead)).json()
    assert body["specialists"] == [] and "No suitable specialist" in body["answer"]


def test_subset_keeps_only_matching_tools() -> None:
    from backend.app.core.config import Settings
    from backend.app.tools.catalog import build_tool_registry

    registry = build_tool_registry(Settings(_env_file=None, environment="test", demo_mode=True))
    finance = subset(registry, SPECIALISTS["finance"][1])
    assert finance.list_tools() and all(
        n.split(".")[0] in {"finance", "crm", "analytics"} for n in finance.list_tools()
    )
    assert "jira.create_task" not in finance.list_tools()
