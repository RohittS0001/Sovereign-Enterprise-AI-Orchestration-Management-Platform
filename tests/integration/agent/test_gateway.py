"""ToolGateway: permissions, validation, approvals and auditing around every tool call."""

import uuid

import pytest
from sqlalchemy import select

from backend.app.core.security.permissions import Principal, Role
from backend.app.db.models import ApprovalRequest, ApprovalStatus, AuditEvent
from backend.app.tools.base import ToolStatus
from backend.app.tools.gateway import ToolGateway
from tests.support.agent import build_registry, fake_jira_service


@pytest.fixture
def jira():
    return fake_jira_service()


@pytest.fixture
async def gateway_factory(database, test_settings, jira):
    sessions = []

    async def make(settings=None):
        session = database.sessionmaker()
        sessions.append(session)
        settings = settings or test_settings
        return ToolGateway(build_registry(settings, jira_service=jira), session, settings), session

    yield make
    for session in sessions:
        await session.close()


async def agent_for(make_user, role: Role) -> Principal:
    user = await make_user(role=role)
    return Principal.human(user.id, user.email, user.role).as_agent()


async def audit_events(session) -> list[AuditEvent]:
    return list(await session.scalars(select(AuditEvent).order_by(AuditEvent.seq)))


async def test_planner_only_sees_permitted_tools(gateway_factory, make_user) -> None:
    gateway, _ = await gateway_factory()

    member = {s.name for s in gateway.available_tools(await agent_for(make_user, Role.MEMBER))}
    lead = {s.name for s in gateway.available_tools(await agent_for(make_user, Role.TEAM_LEAD))}

    assert "jira.get_issue" in member and "gmail.get_email" in member
    assert "jira.create_task" not in member
    assert "jira.create_task" in lead


async def test_read_tool_runs_and_is_audited(gateway_factory, make_user, jira) -> None:
    gateway, session = await gateway_factory()
    agent = await agent_for(make_user, Role.MEMBER)

    result = await gateway.invoke(agent, "jira.get_issue", {"issue_key": "PHX-7"})

    assert result.status == ToolStatus.SUCCESS
    jira.get_issue.assert_awaited_once_with("PHX-7")
    [event] = await audit_events(session)
    assert (event.action, event.status, event.actor_type) == ("tool.invoke", "success", "agent")
    assert event.actor_user_id == agent.user_id
    assert event.resource_id == "jira.get_issue"


async def test_member_agent_is_denied_write_tools(gateway_factory, make_user, jira) -> None:
    gateway, session = await gateway_factory()
    agent = await agent_for(make_user, Role.MEMBER)

    result = await gateway.invoke(agent, "jira.create_task", {"project_key": "PHX", "summary": "x"})

    assert result.status == ToolStatus.DENIED
    jira.create_task.assert_not_awaited()
    assert (await audit_events(session))[-1].status == "denied"


async def test_unknown_tool_and_bad_arguments(gateway_factory, make_user) -> None:
    gateway, session = await gateway_factory()
    agent = await agent_for(make_user, Role.MEMBER)

    unknown = await gateway.invoke(agent, "jira.delete_everything", {})
    invalid = await gateway.invoke(agent, "jira.get_issue", {"issue_key": "../../admin"})

    assert unknown.status == ToolStatus.ERROR and "Unknown tool" in unknown.error
    assert invalid.status == ToolStatus.ERROR and "Invalid arguments" in invalid.error
    assert [e.status for e in await audit_events(session)] == ["failure", "failure"]


async def test_agent_write_is_queued_for_approval(gateway_factory, make_user, jira) -> None:
    gateway, session = await gateway_factory()
    agent = await agent_for(make_user, Role.TEAM_LEAD)

    result = await gateway.invoke(
        agent, "jira.create_task", {"project_key": "PHX", "summary": "Fix gateway"}
    )

    assert result.status == ToolStatus.PENDING_APPROVAL
    jira.create_task.assert_not_awaited()
    approval = await session.get(ApprovalRequest, uuid.UUID(result.approval_id))
    assert approval.status == ApprovalStatus.PENDING
    assert approval.tool_name == "jira.create_task"
    assert approval.arguments["summary"] == "Fix gateway"
    assert approval.requested_by_id == agent.user_id
    assert [e.action for e in await audit_events(session)] == ["approval.request", "tool.invoke"]


async def test_exempt_write_runs_directly_unless_run_is_flagged(
    gateway_factory, make_user, jira, test_settings
) -> None:
    settings = test_settings.model_copy(update={"approval_exempt_tools": ["jira.add_comment"]})
    gateway, _ = await gateway_factory(settings)
    agent = await agent_for(make_user, Role.TEAM_LEAD)
    args = {"issue_key": "PHX-7", "comment": "Looking into it"}

    direct = await gateway.invoke(agent, "jira.add_comment", args)
    flagged = await gateway.invoke(
        agent, "jira.add_comment", args, risk_flags=["possible_prompt_injection"]
    )

    assert direct.status == ToolStatus.SUCCESS
    assert flagged.status == ToolStatus.PENDING_APPROVAL
    assert jira.add_comment.await_count == 1


async def test_human_principal_writes_without_approval(gateway_factory, make_user, jira) -> None:
    gateway, _ = await gateway_factory()
    user = await make_user(role=Role.TEAM_LEAD)
    human = Principal.human(user.id, user.email, user.role)

    result = await gateway.invoke(human, "jira.create_task", {"project_key": "PHX", "summary": "x"})

    assert result.status == ToolStatus.SUCCESS
    jira.create_task.assert_awaited_once()
