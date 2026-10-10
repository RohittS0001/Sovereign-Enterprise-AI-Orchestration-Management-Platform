
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.mcp.schemas.common import ToolResult, ToolStatus


ToolHandler = Callable[
    [dict[str, Any]],
    ToolResult | Awaitable[ToolResult],
]


class ToolRegistry:
    """Register, discover, and invoke synchronous or asynchronous MCP tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        """Register a tool under a unique name."""
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError("Tool name must not be empty")

        if normalized_name in self._tools:
            raise ValueError(f"Tool '{normalized_name}' is already registered")

        self._tools[normalized_name] = handler

    def list_tools(self) -> list[str]:
        """Return registered tool names."""
        return sorted(self._tools)

    async def invoke(
        self,
        name: str,
        payload: dict[str, Any],
    ) -> ToolResult:
        """Invoke a registered synchronous or asynchronous tool."""
        handler = self._tools.get(name.strip())

        if handler is None:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Unknown tool: {name}",
            )

        result = handler(payload)

        if inspect.isawaitable(result):
            return await result

        return result
