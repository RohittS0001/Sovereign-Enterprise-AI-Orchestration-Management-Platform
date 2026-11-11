"""Contract tests for every connector tool: permission, read/write, approval, audit, and that
nothing reaches an integration except through the ToolGateway."""

import ast
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.security.permissions import (
    AGENT_SCOPES,
    APPROVAL_REQUIRED,
    Permission,
    Principal,
    Role,
)
from backend.app.db.models import ApprovalRequest, AuditEvent
from backend.app.integrations.hub import build_integration_hub
from backend.app.tools.base import ToolOperation, ToolStatus
from backend.app.tools.catalog import build_tool_registry
from backend.app.tools.gateway import ToolGateway

ROOT = Path(__file__).resolve().parents[3]
TODAY = date(2026, 11, 9)
WRITE_PERMISSIONS = {Permission.PROJECTS_WRITE, Permission.EMAIL_SEND}

# What each connector category exposes and the permission that guards it. A new tool in a
# category must be added here deliberately (the test fails until it is).
EXPECTED = {
    "tracker": ("read", Permission.PROJECTS_READ),
    "ops": ("read", Permission.PROJECTS_READ),
    "meetings": ("read", Permission.PROJECTS_READ),
    "calendar": ("read", Permission.PEOPLE_READ),  # people's schedules
    "hr": ("read", Permission.PEOPLE_READ),  # leave is personal data
    "keka": ("read", Permission.PEOPLE_READ),
    "finance": ("read", Permission.ANALYTICS_READ),
    "crm": ("read", Permission.ANALYTICS_READ),
    "support": ("read", Permission.ANALYTICS_READ),
}


@pytest.fixture(scope="module")
def registry():
    settings = Settings(_env_file=None, demo_mode=True)
    return build_tool_registry(settings, build_integration_hub(settings, demo_today=TODAY))


def test_every_tool_declares_permission_operation_and_input_schema(registry) -> None:
    for spec in registry.specs():
        assert spec.permission is not None, spec.name
        assert spec.input_model is not None, spec.name  # the gateway validates against it
        if spec.operation == ToolOperation.WRITE:
            # Agent writes always need a human: the permission is in APPROVAL_REQUIRED.
            assert spec.permission in APPROVAL_REQUIRED, spec.name
        else:
            assert spec.permission not in WRITE_PERMISSIONS, spec.name


def test_connector_categories_use_the_expected_permissions(registry) -> None:
    names = {spec.name: spec for spec in registry.specs()}
    for category, (operation, permission) in EXPECTED.items():
        tools = [s for n, s in names.items() if n.startswith(f"{category}.")]
        assert tools, f"no {category}.* tools registered in demo mode"
        for spec in tools:
            assert (spec.operation.value, spec.permission) == (operation, permission), spec.name
    post = names["chat.post_message"]
    assert (post.operation, post.permission) == (ToolOperation.WRITE, Permission.EMAIL_SEND)


def test_agent_scopes_cover_only_what_tools_need() -> None:
    assert APPROVAL_REQUIRED <= AGENT_SCOPES
    assert Permission.AUDIT_READ not in AGENT_SCOPES  # the agent never reads the audit log
    assert Permission.USERS_MANAGE not in AGENT_SCOPES


@pytest.fixture
async def gateway(database, test_settings):
    settings = test_settings.model_copy(update={"demo_mode": True})
    registry = build_tool_registry(settings, build_integration_hub(settings, demo_today=TODAY))
    async with database.session() as session:
        yield ToolGateway(registry, session, settings), session


async def principal(make_user, role: Role) -> Principal:
    user = await make_user(role=role)
    return Principal.human(user.id, user.email, user.role)


async def test_connector_write_by_the_agent_waits_for_approval_and_is_audited(
    gateway, make_user
) -> None:
    gw, session = gateway
    agent = (await principal(make_user, Role.PROJECT_MANAGER)).as_agent()

    result = await gw.invoke(
        agent, "chat.post_message", {"channel": "C0PHX0001", "text": "Release moved to Friday"}
    )

    assert result.status == ToolStatus.PENDING_APPROVAL
    approvals = list(await session.scalars(select(ApprovalRequest)))
    assert [a.tool_name for a in approvals] == ["chat.post_message"]
    actions = [e.action for e in await session.scalars(select(AuditEvent))]
    assert any("approval" in a for a in actions)


@pytest.mark.parametrize(
    ("role", "tool", "arguments"),
    [
        (Role.MEMBER, "hr.list_leave", {"start": "2026-11-01", "end": "2026-11-30"}),
        (Role.MEMBER, "finance.budget_vs_actual", {"project_key": "PHX"}),
        (Role.MEMBER, "crm.list_deals", {"project_key": "PHX"}),
        (Role.AUDITOR, "chat.post_message", {"channel": "C0PHX0001", "text": "hi"}),
    ],
)
async def test_connector_tools_deny_roles_without_the_permission(
    gateway, make_user, role, tool, arguments
) -> None:
    gw, session = gateway
    agent = (await principal(make_user, role)).as_agent()

    result = await gw.invoke(agent, tool, arguments)

    assert result.status == ToolStatus.DENIED
    denied = [e for e in await session.scalars(select(AuditEvent)) if e.status == "denied"]
    assert denied and denied[-1].resource_id == tool


async def test_connector_read_runs_and_is_audited(gateway, make_user) -> None:
    gw, session = gateway
    agent = (await principal(make_user, Role.PROJECT_MANAGER)).as_agent()

    result = await gw.invoke(agent, "ops.list_incidents", {"project_key": "PHX"})

    assert result.status == ToolStatus.SUCCESS
    events = list(await session.scalars(select(AuditEvent)))
    assert events[-1].resource_id == "ops.list_incidents" and events[-1].status == "success"


def test_only_the_gateway_and_approved_executions_call_the_registry() -> None:
    """Every agent/MCP/briefing/backlog path goes through ToolGateway.invoke; the registry is
    invoked directly only by the gateway itself and by approval execution (which runs a call
    the gateway already checked and stored, after the approver's own permission check)."""
    allowed = {
        ROOT / "backend/app/tools/gateway.py",
        ROOT / "backend/app/domains/governance/approvals/service.py",
    }
    offenders = []
    for path in (ROOT / "backend/app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "invoke"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "registry"
                and path not in allowed
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []
