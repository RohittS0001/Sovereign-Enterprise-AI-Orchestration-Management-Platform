"""Approval workflow and the tamper-evident audit log, through the HTTP API."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, update

from backend.app.core.security.permissions import Role
from backend.app.db.base import utcnow
from backend.app.db.models import ApprovalRequest, AuditChainHead, AuditEvent
from backend.app.main import create_app
from tests.conftest import TEST_PASSWORD, serve
from tests.support.agent import DONE, build_registry, fake_jira_service, models, plan

CREATE_TASK = ("jira.create_task", {"project_key": "PHX", "summary": "Escalate vendor"})


@pytest.fixture
def jira():
    return fake_jira_service()


@pytest.fixture
def app_factory(test_settings, database, jira):
    def factory(planner_replies=(), answers=(), settings=None):
        settings = settings or test_settings
        agent_models, _, _ = models(list(planner_replies), list(answers))
        return create_app(
            settings,
            database,
            agent_models=agent_models,
            tool_registry=build_registry(settings, jira_service=jira),
        )

    return factory


async def propose_task(client, headers) -> dict:
    response = await client.post(
        "/api/v1/agent/query", json={"query": "Create a task to escalate"}, headers=headers
    )
    assert response.status_code == 200
    return response.json()


async def test_approving_executes_the_action_and_completes_the_run(
    app_factory, make_user, auth_headers, jira
) -> None:
    requester = auth_headers(await make_user(role=Role.TEAM_LEAD))
    approver = auth_headers(await make_user(role=Role.PROJECT_MANAGER))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."])) as client:
        run = await propose_task(client, requester)
        [approval_id] = run["pending_approvals"]
        assert run["status"] == "awaiting_approval"

        pending = (await client.get("/api/v1/approvals", headers=approver)).json()
        assert [a["id"] for a in pending["items"]] == [approval_id]
        assert "jira.create_task" in pending["items"][0]["summary"]

        decided = await client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={"note": "ok"}, headers=approver
        )
        run_after = await client.get(f"/api/v1/agent/runs/{run['run_id']}", headers=requester)

    assert decided.status_code == 200
    body = decided.json()
    assert body["status"] == "executed"
    assert body["result"] == {"id": "10099", "key": "PHX-99"}
    assert body["decision_note"] == "ok"
    jira.create_task.assert_awaited_once()
    assert run_after.json()["status"] == "completed"


async def test_rejecting_does_not_run_the_action(
    app_factory, make_user, auth_headers, jira
) -> None:
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."])) as client:
        [approval_id] = (await propose_task(client, headers))["pending_approvals"]
        rejected = await client.post(
            f"/api/v1/approvals/{approval_id}/reject", json={"note": "not now"}, headers=headers
        )
        again = await client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers
        )

    assert rejected.json()["status"] == "rejected"
    assert again.status_code == 409
    jira.create_task.assert_not_awaited()


async def test_failed_execution_is_recorded(app_factory, make_user, auth_headers, jira) -> None:
    from backend.app.core.exceptions.exceptions import IntegrationError

    jira.create_task.side_effect = IntegrationError(
        "Jira returned 400", integration="jira", upstream_status=400
    )
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."])) as client:
        [approval_id] = (await propose_task(client, headers))["pending_approvals"]
        decided = (
            await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers)
        ).json()

    assert decided["status"] == "failed"
    assert "Jira returned 400" in decided["error"]
    assert jira.create_task.await_count == 1  # writes are not retried


async def test_four_eyes_policy(app_factory, make_user, auth_headers, test_settings) -> None:
    settings = test_settings.model_copy(update={"approval_allow_self_approval": False})
    requester = auth_headers(await make_user(role=Role.TEAM_LEAD))
    colleague = auth_headers(await make_user(role=Role.TEAM_LEAD))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."], settings)) as client:
        [approval_id] = (await propose_task(client, requester))["pending_approvals"]
        own = await client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={}, headers=requester
        )
        other = await client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={}, headers=colleague
        )

    assert own.status_code == 403
    assert other.status_code == 200


async def test_visibility_and_who_may_decide(
    app_factory, make_user, auth_headers, database
) -> None:
    requester = auth_headers(await make_user(role=Role.TEAM_LEAD))
    member = auth_headers(await make_user(role=Role.MEMBER))
    auditor = auth_headers(await make_user(role=Role.AUDITOR))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."])) as client:
        [approval_id] = (await propose_task(client, requester))["pending_approvals"]

        member_list = (await client.get("/api/v1/approvals", headers=member)).json()
        member_get = await client.get(f"/api/v1/approvals/{approval_id}", headers=member)
        member_decide = await client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={}, headers=member
        )
        auditor_decide = await client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={}, headers=auditor
        )
        own_view = await client.get(f"/api/v1/approvals/{approval_id}", headers=requester)

    assert member_list["total"] == 0
    assert member_get.status_code == 404
    assert member_decide.status_code == 404
    assert auditor_decide.status_code == 404
    assert own_view.status_code == 200


async def test_expired_requests_cannot_be_approved(
    app_factory, make_user, auth_headers, database
) -> None:
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."])) as client:
        run = await propose_task(client, headers)
        async with database.session() as session:
            await session.execute(
                update(ApprovalRequest).values(expires_at=utcnow() - timedelta(minutes=1))
            )
            await session.commit()

        expired = (
            await client.get("/api/v1/approvals", params={"status": "expired"}, headers=headers)
        ).json()
        approve = await client.post(
            f"/api/v1/approvals/{run['pending_approvals'][0]}/approve", json={}, headers=headers
        )
        run_after = (
            await client.get(f"/api/v1/agent/runs/{run['run_id']}", headers=headers)
        ).json()

    assert expired["total"] == 1
    assert approve.status_code == 409
    assert run_after["status"] == "completed"


# ------------------------------------------------------------------ audit log


async def test_audit_log_requires_permission(client, make_user, auth_headers) -> None:
    member = auth_headers(await make_user(role=Role.MEMBER))
    auditor = auth_headers(await make_user(role=Role.AUDITOR))

    assert (await client.get("/api/v1/audit", headers=member)).status_code == 403
    assert (await client.get("/api/v1/audit", headers=auditor)).status_code == 200


async def test_logins_and_agent_activity_are_audited(app_factory, make_user, auth_headers) -> None:
    await make_user(email="lead@example.com", role=Role.TEAM_LEAD)
    auditor = auth_headers(await make_user(role=Role.AUDITOR))

    async with serve(app_factory([plan(CREATE_TASK), DONE], ["Proposed."])) as client:
        await client.post(
            "/api/v1/auth/login", json={"email": "lead@example.com", "password": "Wr0ng!Passw0rd"}
        )
        tokens = (
            await client.post(
                "/api/v1/auth/login", json={"email": "lead@example.com", "password": TEST_PASSWORD}
            )
        ).json()
        await propose_task(client, {"Authorization": f"Bearer {tokens['access_token']}"})

        events = (await client.get("/api/v1/audit", params={"limit": 50}, headers=auditor)).json()
        logins = (
            await client.get("/api/v1/audit", params={"action": "auth.login"}, headers=auditor)
        ).json()

    actions_in_order = [(e["action"], e["status"]) for e in reversed(events["items"])]
    assert actions_in_order == [
        ("auth.login", "failure"),
        ("auth.login", "success"),
        ("approval.request", "pending"),
        ("tool.invoke", "pending"),
        ("agent.run", "success"),
    ]
    assert logins["total"] == 2
    agent_events = [e for e in events["items"] if e["action"] in {"tool.invoke", "agent.run"}]
    assert all(e["actor_type"] == "agent" for e in agent_events)


async def test_audit_details_are_redacted(app_factory, make_user, auth_headers, database) -> None:
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))
    secret_task = (
        "jira.create_task",
        {"project_key": "PHX", "summary": "rotate password: S3cr3t!!"},
    )

    async with serve(app_factory([plan(secret_task), DONE], ["ok"])) as client:
        await propose_task(client, headers)

    async with database.session() as session:
        from sqlalchemy import select

        details = [e.details for e in await session.scalars(select(AuditEvent))]
    assert "S3cr3t!!" not in str(details)
    assert "[REDACTED:SECRET]" in str(details)


async def _generate_events(client, make_user, count: int = 3) -> None:
    for _ in range(count):
        await make_user(email=f"u{_}@example.com")
        await client.post(
            "/api/v1/auth/login", json={"email": f"u{_}@example.com", "password": TEST_PASSWORD}
        )


@pytest.mark.parametrize("tamper", ["edit", "delete_middle", "delete_last", "none"])
async def test_audit_chain_detects_tampering(
    client, make_user, auth_headers, database, tamper
) -> None:
    auditor = auth_headers(await make_user(role=Role.AUDITOR, email="aud@example.com"))
    await _generate_events(client, make_user)

    async with database.session() as session:
        if tamper == "edit":
            await session.execute(
                update(AuditEvent).where(AuditEvent.seq == 2).values(status="failure")
            )
        elif tamper == "delete_middle":
            await session.execute(delete(AuditEvent).where(AuditEvent.seq == 2))
        elif tamper == "delete_last":
            await session.execute(delete(AuditEvent).where(AuditEvent.seq == 3))
        await session.commit()

    result = (await client.get("/api/v1/audit/verify", headers=auditor)).json()

    expected = {
        "none": (True, None, None),
        "edit": (False, 2, "content modified"),
        "delete_middle": (False, 2, "missing event"),
        "delete_last": (False, 3, "events missing at end"),
    }[tamper]
    assert (result["valid"], result["first_invalid_seq"], result["reason"]) == expected


async def test_chain_head_tracks_latest_event(client, make_user, database) -> None:
    await _generate_events(client, make_user, count=2)

    async with database.session() as session:
        head = await session.get(AuditChainHead, 1)
        last = await session.get(AuditEvent, 2)

    assert head.last_seq == 2
    assert head.last_hash == last.hash
