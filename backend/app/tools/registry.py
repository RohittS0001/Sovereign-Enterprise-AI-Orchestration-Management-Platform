"""Catalog of tools the agent can call, with input schemas and execution safeguards.

The registry handles mechanics (validation, timeouts, retries). Governance —
permissions, approvals and auditing — lives in ``ToolGateway``.
"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.app.core.logging import get_logger
from backend.app.core.security.permissions import Permission
from backend.app.tools.base import ToolOperation, ToolResult, ToolStatus

logger = get_logger(__name__)

ToolHandler = Callable[
    [dict[str, Any]],
    ToolResult | Awaitable[ToolResult],
]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: ToolHandler
    description: str = ""
    input_model: type[BaseModel] | None = None
    operation: ToolOperation = ToolOperation.READ
    permission: Permission | None = None
    timeout_seconds: float | None = None

    @property
    def is_write(self) -> bool:
        return self.operation == ToolOperation.WRITE

    def input_schema(self) -> dict[str, Any]:
        if self.input_model is None:
            return {"type": "object", "properties": {}}
        return self.input_model.model_json_schema()

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized arguments or raise ``ValidationError``."""
        if self.input_model is None:
            return dict(payload)
        return self.input_model.model_validate(payload).model_dump(mode="json")


class ToolRegistry:
    """Register, discover, and invoke synchronous or asynchronous MCP tools."""

    def __init__(
        self,
        *,
        default_timeout: float = 30.0,
        read_retries: int = 0,
        retry_backoff: float = 0.5,
    ) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.default_timeout = default_timeout
        self.read_retries = read_retries
        self.retry_backoff = retry_backoff

    def register(
        self,
        name: str,
        handler: ToolHandler,
        *,
        description: str = "",
        input_model: type[BaseModel] | None = None,
        operation: ToolOperation = ToolOperation.READ,
        permission: Permission | None = None,
        timeout_seconds: float | None = None,
    ) -> ToolSpec:
        """Register a tool under a unique name."""
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError("Tool name must not be empty")

        if normalized_name in self._tools:
            raise ValueError(f"Tool '{normalized_name}' is already registered")

        spec = ToolSpec(
            name=normalized_name,
            handler=handler,
            description=description,
            input_model=input_model,
            operation=operation,
            permission=permission,
            timeout_seconds=timeout_seconds,
        )
        self._tools[normalized_name] = spec
        return spec

    def list_tools(self) -> list[str]:
        """Return registered tool names."""
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name.strip())

    def specs(self) -> list[ToolSpec]:
        return [self._tools[name] for name in self.list_tools()]

    async def invoke(
        self,
        name: str,
        payload: dict[str, Any],
    ) -> ToolResult:
        """Validate input and invoke a tool with a timeout (and retries for reads)."""
        spec = self.get(name)

        if spec is None:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Unknown tool: {name}",
            )

        try:
            arguments = spec.validate(payload)
        except ValidationError as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Invalid arguments for {spec.name}: {exc.errors(include_url=False)}",
            )

        # Writes are never retried automatically: they may not be idempotent.
        attempts = 1 + (0 if spec.is_write else self.read_retries)
        result = ToolResult(status=ToolStatus.ERROR, error="Tool was not executed")

        for attempt in range(1, attempts + 1):
            result = await self._call(spec, arguments)
            if result.status != ToolStatus.ERROR or not result.retryable or attempt == attempts:
                break
            delay = self.retry_backoff * (2 ** (attempt - 1))
            logger.info(
                "Retrying tool",
                extra={"tool": spec.name, "attempt": attempt + 1, "delay_s": delay},
            )
            await asyncio.sleep(delay)

        return result

    async def _call(self, spec: ToolSpec, arguments: dict[str, Any]) -> ToolResult:
        timeout = spec.timeout_seconds or self.default_timeout
        try:
            async with asyncio.timeout(timeout):
                result = spec.handler(arguments)
                if inspect.isawaitable(result):
                    result = await result
        except TimeoutError:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"{spec.name} timed out after {timeout:g}s",
                retryable=True,
            )
        except Exception as exc:
            logger.exception("Tool raised", extra={"tool": spec.name})
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"{spec.name} failed: {exc.__class__.__name__}",
            )

        if not isinstance(result, ToolResult):
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"{spec.name} returned an invalid result",
            )
        return result
