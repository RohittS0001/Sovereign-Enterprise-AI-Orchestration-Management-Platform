import json

from backend.app.core.security.permissions import Role
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, models, plan


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append({"event": lines["event"], **json.loads(lines["data"])})
    return events


async def test_stream_emits_steps_then_final_run(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    agent_models, _, _ = models(
        [
            plan(
                ("jira.get_issue", {"issue_key": "PHX-7"}),
                ("jira.create_task", {"project_key": "PHX", "summary": "x"}),
            ),
            DONE,
        ],
        ["PHX-7 is blocked [S1]."],
    )
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))

    async with serve(create_app(settings, database, agent_models=agent_models)) as client:
        response = await client.post(
            "/api/v1/agent/query/stream", json={"query": "status of PHX-7?"}, headers=headers
        )

    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    kinds = [e["event"] for e in events]
    assert kinds == ["run_started", "step", "step", "phase", "done"]
    assert [e["step"]["status"] for e in events if e["event"] == "step"] == [
        "success",
        "pending_approval",
    ]
    final = events[-1]["run"]
    assert final["run_id"] == events[0]["run_id"]
    assert final["status"] == "awaiting_approval" and final["answer"] == "PHX-7 is blocked [S1]."


async def test_stream_requires_auth_and_model(client, make_user, auth_headers) -> None:
    unauthenticated = await client.post("/api/v1/agent/query/stream", json={"query": "hi"})
    no_model = await client.post(
        "/api/v1/agent/query/stream", json={"query": "hi"}, headers=auth_headers(await make_user())
    )

    assert unauthenticated.status_code == 401
    assert no_model.status_code == 503
