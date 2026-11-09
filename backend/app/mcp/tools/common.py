"""Shared execution wrapper for async MCP tools."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.app.core.exceptions.exceptions import IntegrationError
from backend.app.mcp.schemas.common import ToolResult, ToolStatus


async def run_tool[T: BaseModel](
    label: str,
    operation: str,
    payload: dict[str, Any],
    input_model: type[T],
    handler: Callable[[T], Awaitable[Any]],
) -> ToolResult:
    """Validate input, call the service, and turn every failure into a ToolResult."""
    try:
        inputs = input_model.model_validate(payload)
        return ToolResult(status=ToolStatus.SUCCESS, data=await handler(inputs))
    except ValidationError as exc:
        return ToolResult(
            status=ToolStatus.ERROR, error=f"Invalid {label} input for {operation}: {exc}"
        )
    except IntegrationError as exc:
        status = exc.upstream_status
        return ToolResult(
            status=ToolStatus.ERROR,
            error=f"Failed to {operation}: {exc}",
            retryable=status is None or status == 429 or status >= 500,
        )
    except ValueError as exc:
        return ToolResult(
            status=ToolStatus.ERROR, error=f"Invalid {label} input for {operation}: {exc}"
        )
    except Exception as exc:
        return ToolResult(status=ToolStatus.ERROR, error=f"Failed to {operation}: {exc}")
