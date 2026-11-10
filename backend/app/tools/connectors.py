"""Category tools over the connector framework: one tool per operation, any vendor.

``tracker.search_work_items`` searches Linear, GitLab, Azure DevOps, ... (every configured
tracker, or the one named in ``source``). A failing source becomes a warning in the result
instead of failing the whole call. Tools never raise (``run_tool``).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.integrations.hub import IntegrationHub
from backend.app.tools.base import ToolResult
from backend.app.tools.runner import run_tool

Status = Literal["todo", "in_progress", "done"]


def _dump(value: Any) -> Any:
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


class SourceInput(BaseModel):
    source: str | None = Field(
        default=None, description="One connector, e.g. 'linear'; default: all"
    )


# ------------------------------------------------------------------ tracker inputs


class SearchWorkItemsInput(SourceInput):
    project: str | None = Field(default=None, description="Team / project / list, per source")
    query: str | None = Field(default=None, max_length=200, description="Text in key or title")
    status: Status | None = None
    limit: int = Field(default=25, ge=1, le=50)


class GetWorkItemInput(BaseModel):
    source: str = Field(description="Connector that owns the item, e.g. 'gitlab'")
    key: str = Field(min_length=1, max_length=200, description="e.g. PLAT-41, group/repo#12, 1042")


class ListPullRequestsInput(SourceInput):
    repository: str | None = Field(default=None, max_length=200)
    state: Literal["open", "merged", "closed", "all"] = "open"
    limit: int = Field(default=25, ge=1, le=50)


# ------------------------------------------------------------------ ops inputs


class OpsInput(SourceInput):
    scope: list[str] = Field(
        default_factory=list, max_length=20,
        description="Records to read: PagerDuty service ids, Sentry project slugs, Jenkins jobs, "
        "Argo CD apps, SonarQube project keys (see the project's links)",
    )  # fmt: skip
    limit: int = Field(default=25, ge=1, le=50)


class IncidentsInput(OpsInput):
    open_only: bool = True


class ConnectorTools:
    def __init__(self, hub: IntegrationHub) -> None:
        self.hub = hub

    def sources(self, category: str) -> list[str]:
        return sorted(self.hub.by_category(category))

    def _pick(
        self, category: str, source: str | None, capability: str | None = None
    ) -> dict[str, Any]:
        services = {
            name: svc for name, svc in self.hub.by_category(category).items()
            if capability is None or callable(getattr(svc, capability, None))
        }  # fmt: skip
        if source is None:
            return services
        if source not in services:
            raise ValueError(f"Unknown {category} source {source!r}; available: {sorted(services)}")
        return {source: services[source]}

    async def _fan_out(
        self, category: str, source: str | None, call: Callable[[Any], Awaitable[Any]],
        capability: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        services = self._pick(category, source, capability)
        names = list(services)
        outcomes = await asyncio.gather(*(call(services[n]) for n in names), return_exceptions=True)
        results: list[Any] = []
        warnings: dict[str, str] = {}
        for name, outcome in zip(names, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                warnings[name] = (
                    getattr(outcome, "message", None) or str(outcome) or type(outcome).__name__
                )
            else:
                results.extend(_dump(outcome) if isinstance(outcome, list) else [_dump(outcome)])
        if len(names) == 1 and warnings and not results:
            raise ValueError(next(iter(warnings.values())))
        return {"sources": names, "results": results, "warnings": warnings}

    # ---------------------------------------------------------------- tracker

    async def search_work_items(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "tracker", "search work items", payload, SearchWorkItemsInput,
            lambda i: self._fan_out("tracker", i.source, lambda s: s.search_work_items(
                project=i.project, query=i.query, status=i.status, limit=i.limit)),
        )  # fmt: skip

    async def get_work_item(self, payload: dict[str, Any]) -> ToolResult:
        async def get(i: GetWorkItemInput) -> Any:
            service = self._pick("tracker", i.source)[i.source]
            return _dump(await service.get_work_item(i.key))

        return await run_tool("tracker", "get work item", payload, GetWorkItemInput, get)

    async def list_pull_requests(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "tracker", "list pull requests", payload, ListPullRequestsInput,
            lambda i: self._fan_out("tracker", i.source, lambda s: s.list_pull_requests(
                repository=i.repository, state=i.state, limit=i.limit)),
        )  # fmt: skip

    # ---------------------------------------------------------------- ops

    async def list_incidents(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops", "list incidents", payload, IncidentsInput,
            lambda i: self._fan_out("ops", i.source, lambda s: s.list_incidents(
                open_only=i.open_only, scope=i.scope or None, limit=i.limit), "list_incidents"),
        )  # fmt: skip

    async def list_errors(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops", "list errors", payload, OpsInput,
            lambda i: self._fan_out("ops", i.source, lambda s: s.list_errors(
                scope=i.scope or None, limit=i.limit), "list_errors"),
        )  # fmt: skip

    async def list_deployments(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops", "list deployments", payload, OpsInput,
            lambda i: self._fan_out("ops", i.source, lambda s: s.list_deployments(
                scope=i.scope or None, limit=i.limit), "list_deployments"),
        )  # fmt: skip

    async def quality_gates(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops", "quality gates", payload, OpsInput,
            lambda i: self._fan_out("ops", i.source, lambda s: s.quality_gates(
                scope=i.scope or None), "quality_gates"),
        )  # fmt: skip
