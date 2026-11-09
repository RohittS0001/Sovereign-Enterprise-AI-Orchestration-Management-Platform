"""Integration status API and a full agent run on the demo dataset."""

from backend.app.core.security.permissions import Role
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, models, plan


async def test_status_requires_admin(client, make_user, auth_headers) -> None:
    lead = auth_headers(await make_user(role=Role.TEAM_LEAD))

    assert (await client.get("/api/v1/integrations", headers=lead)).status_code == 403


async def test_status_without_configuration(client, make_user, auth_headers) -> None:
    admin = auth_headers(await make_user(role=Role.ADMIN))

    body = (await client.get("/api/v1/integrations?check=true", headers=admin)).json()

    core = [i for i in body if i["category"] == "core"]
    assert [(i["name"], i["mode"], i["tools"]) for i in core] == [
        ("jira", "disabled", []),
        ("github", "disabled", []),
        ("keka", "disabled", []),
        ("gmail", "disabled", []),
    ]
    assert all(i["mode"] == "disabled" for i in body)
    jira = core[0]
    assert jira["missing_env"] == ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]
    assert jira["setup"] and jira["docs_url"].startswith("https://")


async def test_demo_mode_enables_every_integration(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    admin = auth_headers(await make_user(role=Role.ADMIN))

    async with serve(create_app(settings, database)) as client:
        body = (await client.get("/api/v1/integrations?check=true", headers=admin)).json()

    status = {i["name"]: i for i in body}
    active = [i for i in body if i["mode"] != "disabled"]
    assert {"jira", "github", "keka", "gmail"} <= set(status)
    assert all(
        i["mode"] == "demo" and i["healthy"] is True and not i["missing_env"] for i in active
    )
    assert "github.list_workflow_runs" in status["github"]["tools"]
    assert "keka.get_leave_requests" in status["keka"]["tools"]
    assert "gmail.create_draft" in status["gmail"]["tools"]


async def test_live_health_check_reports_failures(
    test_settings, database, make_user, auth_headers
) -> None:
    from pydantic import SecretStr

    # GitHub configured but pointed at an address that refuses connections.
    settings = test_settings.model_copy(
        update={"github_token": SecretStr("t"), "github_api_url": "http://127.0.0.1:9"}
    )
    admin = auth_headers(await make_user(role=Role.ADMIN))

    async with serve(create_app(settings, database)) as client:
        body = (await client.get("/api/v1/integrations?check=true", headers=admin)).json()

    github = next(i for i in body if i["name"] == "github")
    assert github["mode"] == "live"
    assert github["healthy"] is False
    assert "Could not reach GitHub" in github["detail"]


async def test_agent_combines_jira_github_and_keka_in_demo_mode(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    agent_models, ops, reporting = models(
        [
            plan(
                ("jira.get_issue", {"issue_key": "PHX-7"}),
                ("github.search_pull_requests", {"query": "PHX-7"}),
                ("keka.search_employees", {"search": "Arjun"}),
            ),
            plan(("keka.get_leave_requests", {
                "from_date": "2026-11-01", "to_date": "2026-11-30", "employee_ids": ["e-002"]})),
            DONE,
        ],
        ["PHX-7 is blocked [S1]; fix PR #102 is a draft [S2]; Arjun is on leave soon [S4]."],
    )  # fmt: skip
    headers = auth_headers(await make_user(role=Role.PROJECT_MANAGER))

    async with serve(create_app(settings, database, agent_models=agent_models)) as client:
        run = (
            await client.post(
                "/api/v1/agent/query", json={"query": "Is PHX-7 at risk?"}, headers=headers
            )
        ).json()

    assert run["status"] == "completed"
    assert [(s["tool"], s["status"]) for s in run["steps"]] == [
        ("jira.get_issue", "success"),
        ("github.search_pull_requests", "success"),
        ("keka.search_employees", "success"),
        ("keka.get_leave_requests", "success"),
    ]
    assert run["citations"] == ["S1", "S2", "S4"]
    evidence = reporting.calls[0]["messages"][1].content
    assert "Vendor gateway timeouts" in evidence and "phx-7-fallback" in evidence
    # The planner was offered the new integrations' tools.
    assert "keka.get_attendance" in ops.calls[0]["messages"][0].content


async def test_demo_injection_email_is_flagged(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    agent_models, _, _ = models(
        [
            plan(("gmail.get_email", {"message_id": "m-3"})),
            plan(("gmail.create_draft", {
                "to": ["verify@acme-support.example"], "subject": "Salary sheet", "body": "attached"})),
            DONE,
        ],
        ["The email looks like a phishing/prompt-injection attempt [S1]."],
    )  # fmt: skip
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))

    async with serve(create_app(settings, database, agent_models=agent_models)) as client:
        run = (
            await client.post(
                "/api/v1/agent/query", json={"query": "Handle my inbox"}, headers=headers
            )
        ).json()

    assert run["flagged_injection"] is True
    assert run["steps"][0]["suspicious"] is True
    assert run["steps"][1]["status"] == "pending_approval"  # never sent or drafted without a human
