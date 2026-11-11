"""Races that must resolve to "at most once": approving one request, rotating one token.

These run against a file-based SQLite database (separate connections, real locking) and use
a barrier so both requests pass their checks before either one writes: the window in which
a check-then-act implementation would execute twice.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from backend.app.core.security.permissions import Role
from backend.app.db.models import RefreshToken
from backend.app.domains.governance.approvals.service import ApprovalService
from backend.app.domains.identity.auth_service import AuthService
from backend.app.main import create_app
from tests.conftest import TEST_PASSWORD, serve
from tests.support.agent import DONE, build_registry, fake_jira_service, models, plan

CREATE_TASK = ("jira.create_task", {"project_key": "PHX", "summary": "Escalate vendor"})


@pytest.fixture
def test_settings(test_settings, tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'race.db').as_posix()}"
    return test_settings.model_copy(update={"database_url": url})


def barrier_before(monkeypatch, cls, name: str, parties: int = 2) -> None:
    """Make ``cls.name`` wait until ``parties`` callers have reached it."""
    barrier = asyncio.Barrier(parties)
    original = getattr(cls, name)

    if asyncio.iscoroutinefunction(original):

        async def wrapped(self, *args, **kwargs):
            result = await original(self, *args, **kwargs)
            await asyncio.wait_for(barrier.wait(), timeout=5)
            return result

    else:  # pragma: no cover - not used
        raise TypeError(name)
    monkeypatch.setattr(cls, name, wrapped)


async def test_two_simultaneous_approvals_execute_the_action_once(
    test_settings, database, make_user, auth_headers, monkeypatch
) -> None:
    jira = fake_jira_service()
    agent_models, _, _ = models([plan(CREATE_TASK), DONE], ["Proposed."])
    app = create_app(
        test_settings,
        database,
        agent_models=agent_models,
        tool_registry=build_registry(test_settings, jira_service=jira),
    )
    requester = auth_headers(await make_user(role=Role.TEAM_LEAD))
    approvers = [auth_headers(await make_user(role=Role.PROJECT_MANAGER)) for _ in range(2)]

    async with serve(app) as client:
        run = (
            await client.post("/api/v1/agent/query", json={"query": "Escalate"}, headers=requester)
        ).json()
        [approval_id] = run["pending_approvals"]
        # Both deciders load the PENDING request before either claims it.
        barrier_before(monkeypatch, ApprovalService, "get")
        responses = await asyncio.gather(
            *(
                client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=h)
                for h in approvers
            )
        )
        monkeypatch.undo()
        final = (await client.get(f"/api/v1/approvals/{approval_id}", headers=approvers[0])).json()

    assert sorted(r.status_code for r in responses) == [200, 409]
    jira.create_task.assert_awaited_once()  # the external write happened exactly once
    assert final["status"] == "executed"


async def test_a_rejection_racing_an_approval_runs_nothing_twice(
    test_settings, database, make_user, auth_headers, monkeypatch
) -> None:
    jira = fake_jira_service()
    agent_models, _, _ = models([plan(CREATE_TASK), DONE], ["Proposed."])
    app = create_app(
        test_settings,
        database,
        agent_models=agent_models,
        tool_registry=build_registry(test_settings, jira_service=jira),
    )
    requester = auth_headers(await make_user(role=Role.TEAM_LEAD))
    a, b = [auth_headers(await make_user(role=Role.PROJECT_MANAGER)) for _ in range(2)]

    async with serve(app) as client:
        run = (
            await client.post("/api/v1/agent/query", json={"query": "Escalate"}, headers=requester)
        ).json()
        [approval_id] = run["pending_approvals"]
        barrier_before(monkeypatch, ApprovalService, "get")
        approve, reject = await asyncio.gather(
            client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=a),
            client.post(f"/api/v1/approvals/{approval_id}/reject", json={}, headers=b),
        )

    assert sorted([approve.status_code, reject.status_code]) == [200, 409]
    assert jira.create_task.await_count == (1 if approve.status_code == 200 else 0)


async def test_concurrent_refreshes_with_one_token_issue_one_replacement(
    test_settings, database, make_user, monkeypatch
) -> None:
    user = await make_user(email="race@example.com")
    app = create_app(test_settings, database)

    async with serve(app) as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": "race@example.com", "password": TEST_PASSWORD}
        )
        refresh_token = login.json()["refresh_token"]
        # Both requests find the token unrevoked before either rotates it.
        barrier_before(monkeypatch, AuthService, "_check_user_for_refresh")
        responses = await asyncio.gather(
            *(
                client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
                for _ in range(2)
            )
        )
        monkeypatch.undo()
        winner = next(r for r in responses if r.status_code == 200)
        # The winner's new token keeps working: the losing request did not revoke sessions.
        again = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": winner.json()["refresh_token"]}
        )
        # Replaying the original token afterwards is still treated as theft.
        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        after_replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": again.json()["refresh_token"]}
        )

    assert sorted(r.status_code for r in responses) == [200, 401]
    assert again.status_code == 200
    assert replay.status_code == 401 and after_replay.status_code == 401
    async with database.session() as session:
        replacements = await session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.replaced_by_id.is_not(None))
        )
    assert replacements == 2  # original → winner → again; the loser's token was rolled back
