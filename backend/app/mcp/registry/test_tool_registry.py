
from unittest.mock import AsyncMock

import pytest

from backend.app.mcp.registry.tool_registry import ToolRegistry
from backend.app.mcp.schemas.common import ToolResult, ToolStatus


def test_register_and_list_tools() -> None:
    registry = ToolRegistry()

    registry.register(
        "email.profile",
        lambda payload: ToolResult(status=ToolStatus.SUCCESS),
    )
    registry.register(
        "jira.project",
        lambda payload: ToolResult(status=ToolStatus.SUCCESS),
    )

    assert registry.list_tools() == ["email.profile", "jira.project"]


@pytest.mark.asyncio
async def test_invoke_sync_handler() -> None:
    registry = ToolRegistry()
    expected = ToolResult(status=ToolStatus.SUCCESS, data={"email": "test@example.com"})

    handler = lambda payload: expected
    registry.register("email.profile", handler)

    result = await registry.invoke("email.profile", {})

    assert result == expected


@pytest.mark.asyncio
async def test_invoke_async_handler() -> None:
    registry = ToolRegistry()
    expected = ToolResult(status=ToolStatus.SUCCESS, data={"key": "DEMO"})
    handler = AsyncMock(return_value=expected)

    registry.register("jira.project", handler)

    result = await registry.invoke("jira.project", {"project_key": "DEMO"})

    assert result == expected
    handler.assert_awaited_once_with({"project_key": "DEMO"})


@pytest.mark.asyncio
async def test_unknown_tool_returns_error() -> None:
    registry = ToolRegistry()

    result = await registry.invoke("missing.tool", {})

    assert result.status == ToolStatus.ERROR
    assert result.error == "Unknown tool: missing.tool"


def test_duplicate_tool_registration_is_rejected() -> None:
    registry = ToolRegistry()
    handler = lambda payload: ToolResult(status=ToolStatus.SUCCESS)

    registry.register("jira.project", handler)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("jira.project", handler)


def test_empty_tool_name_is_rejected() -> None:
    registry = ToolRegistry()
    handler = lambda payload: ToolResult(status=ToolStatus.SUCCESS)

    with pytest.raises(ValueError, match="must not be empty"):
        registry.register("   ", handler)
