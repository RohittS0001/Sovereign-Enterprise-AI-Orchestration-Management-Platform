"""Category tools over the connector framework: one tool per operation, any vendor.

``tracker.search_work_items`` searches Linear, GitLab, Azure DevOps, ... (every configured
tracker, or the one named in ``source``). A failing source becomes a warning in the result
instead of failing the whole call. Tools never raise (``run_tool``).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
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
        default_factory=list,
        max_length=20,
        description="Records to read: PagerDuty service ids, Sentry project slugs, Jenkins jobs, "
        "Argo CD apps, SonarQube project keys (see the project's links)",
    )
    limit: int = Field(default=25, ge=1, le=50)


class IncidentsInput(OpsInput):
    open_only: bool = True


# ------------------------------------------------------------------ hr / finance inputs


class LeaveInput(SourceInput):
    from_date: date
    to_date: date
    employee_ids: list[str] = Field(default_factory=list, max_length=100)


class BudgetInput(SourceInput):
    scope: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Project or cost-centre names; default: all",
    )


class DealsInput(SourceInput):
    project_key: str | None = Field(default=None, description="Jira project key, e.g. PHX")
    open_only: bool = True
    limit: int = Field(default=25, ge=1, le=100)


class TicketsInput(SourceInput):
    project_key: str = Field(description="Project key, e.g. PHX (tickets tagged with it)")
    open_only: bool = True
    limit: int = Field(default=25, ge=1, le=100)


class FreeSlotsInput(SourceInput):
    people: list[str] = Field(min_length=1, max_length=20, description="Email addresses")
    from_date: date
    to_date: date
    duration_minutes: int = Field(default=30, ge=15, le=480)


class EventsInput(SourceInput):
    from_date: date
    to_date: date
    limit: int = Field(default=25, ge=1, le=100)


class MeetingsInput(SourceInput):
    from_date: date
    to_date: date
    limit: int = Field(default=20, ge=1, le=50)


class MeetingActionsInput(BaseModel):
    source: str = Field(description="Meeting source, e.g. 'zoom'")
    meeting_id: str = Field(min_length=1, max_length=300)
    from_date: date = Field(description="Window to look the meeting up in")
    to_date: date


def _window(start: date, end: date, max_days: int = 31) -> tuple[datetime, datetime]:
    if end < start or (end - start).days > max_days:
        raise ValueError(f"date range must be forward and at most {max_days} days")
    return datetime.combine(start, time(0), UTC), datetime.combine(end, time(0), UTC) + timedelta(
        days=1
    )


class PostMessageInput(BaseModel):
    source: str = Field(default="slack", description="'slack' or 'teams_chat'")
    channel: str | None = Field(
        default=None, description="Slack channel id or #name (default: digest channel)"
    )
    text: str = Field(min_length=1, max_length=3900)


class ConnectorTools:
    def __init__(self, hub: IntegrationHub, reporting_model: Any = None) -> None:
        self.hub = hub
        self.reporting_model = reporting_model  # LLM 2 for meeting outcomes (optional)

    def sources(self, category: str) -> list[str]:
        return sorted(self.hub.by_category(category))

    def _pick(
        self, category: str, source: str | None, capability: str | None = None
    ) -> dict[str, Any]:
        services = {
            name: svc
            for name, svc in self.hub.by_category(category).items()
            if capability is None or callable(getattr(svc, capability, None))
        }
        if source is None:
            return services
        if source not in services:
            raise ValueError(f"Unknown {category} source {source!r}; available: {sorted(services)}")
        return {source: services[source]}

    async def _fan_out(
        self,
        category: str,
        source: str | None,
        call: Callable[[Any], Awaitable[Any]],
        capability: str | None = None,
    ) -> dict[str, Any]:
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
            "tracker",
            "search work items",
            payload,
            SearchWorkItemsInput,
            lambda i: self._fan_out(
                "tracker",
                i.source,
                lambda s: s.search_work_items(
                    project=i.project, query=i.query, status=i.status, limit=i.limit
                ),
            ),
        )

    async def get_work_item(self, payload: dict[str, Any]) -> ToolResult:
        async def get(i: GetWorkItemInput) -> Any:
            service = self._pick("tracker", i.source)[i.source]
            return _dump(await service.get_work_item(i.key))

        return await run_tool("tracker", "get work item", payload, GetWorkItemInput, get)

    async def list_pull_requests(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "tracker",
            "list pull requests",
            payload,
            ListPullRequestsInput,
            lambda i: self._fan_out(
                "tracker",
                i.source,
                lambda s: s.list_pull_requests(
                    repository=i.repository, state=i.state, limit=i.limit
                ),
            ),
        )

    # ---------------------------------------------------------------- ops

    async def list_incidents(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops",
            "list incidents",
            payload,
            IncidentsInput,
            lambda i: self._fan_out(
                "ops",
                i.source,
                lambda s: s.list_incidents(
                    open_only=i.open_only, scope=i.scope or None, limit=i.limit
                ),
                "list_incidents",
            ),
        )

    async def list_errors(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops",
            "list errors",
            payload,
            OpsInput,
            lambda i: self._fan_out(
                "ops",
                i.source,
                lambda s: s.list_errors(scope=i.scope or None, limit=i.limit),
                "list_errors",
            ),
        )

    async def list_deployments(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops",
            "list deployments",
            payload,
            OpsInput,
            lambda i: self._fan_out(
                "ops",
                i.source,
                lambda s: s.list_deployments(scope=i.scope or None, limit=i.limit),
                "list_deployments",
            ),
        )

    async def quality_gates(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "ops",
            "quality gates",
            payload,
            OpsInput,
            lambda i: self._fan_out(
                "ops", i.source, lambda s: s.quality_gates(scope=i.scope or None), "quality_gates"
            ),
        )

    # ---------------------------------------------------------------- hr / finance

    async def list_leave(self, payload: dict[str, Any]) -> ToolResult:
        async def run(i: LeaveInput) -> Any:
            if i.to_date < i.from_date or (i.to_date - i.from_date).days > 92:
                raise ValueError("date range must be forward and at most 92 days")
            return await self._fan_out(
                "hr",
                i.source,
                lambda s: s.list_leave(i.from_date, i.to_date, i.employee_ids or None),
            )

        return await run_tool("hr", "list leave", payload, LeaveInput, run)

    async def budget_vs_actual(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "finance",
            "budget vs actual",
            payload,
            BudgetInput,
            lambda i: self._fan_out(
                "finance", i.source, lambda s: s.budget_lines(scope=i.scope or None)
            ),
        )

    # ---------------------------------------------------------------- crm

    async def list_deals(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "crm",
            "list deals",
            payload,
            DealsInput,
            lambda i: self._fan_out(
                "crm",
                i.source,
                lambda s: s.list_deals(project=i.project_key, open_only=i.open_only, limit=i.limit),
            ),
        )

    async def list_tickets(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "support",
            "list tickets",
            payload,
            TicketsInput,
            lambda i: self._fan_out(
                "support",
                i.source,
                lambda s: s.list_tickets(
                    [i.project_key.lower()], open_only=i.open_only, limit=i.limit
                ),
            ),
        )

    # ---------------------------------------------------------------- calendar & meetings

    async def find_free_slots(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.integrations.connectors.calendar_meetings import free_slots

        async def run(i: FreeSlotsInput) -> Any:
            start, end = _window(i.from_date, i.to_date, 14)
            busy = await self._fan_out(
                "calendar", i.source, lambda s: s.free_busy(i.people, start, end)
            )
            from backend.app.integrations.connectors.base import BusySlot

            slots = [BusySlot(**b) for b in busy["results"]]
            return {
                "sources": busy["sources"],
                "warnings": busy["warnings"],
                "free": free_slots(slots, start, end, i.duration_minutes),
                "note": "Weekdays 09:00-18:00 UTC; busy, tentative and out-of-office count as busy.",
            }

        return await run_tool("calendar", "find free slots", payload, FreeSlotsInput, run)

    async def list_events(self, payload: dict[str, Any]) -> ToolResult:
        async def run(i: EventsInput) -> Any:
            start, end = _window(i.from_date, i.to_date)
            return await self._fan_out(
                "calendar", i.source, lambda s: s.list_events(start, end, i.limit)
            )

        return await run_tool("calendar", "list events", payload, EventsInput, run)

    async def list_meetings(self, payload: dict[str, Any]) -> ToolResult:
        async def run(i: MeetingsInput) -> Any:
            _window(i.from_date, i.to_date)
            return await self._fan_out(
                "meetings",
                i.source,
                lambda s: s.list_transcripts(i.from_date, i.to_date, i.limit, with_text=False),
            )

        return await run_tool("meetings", "list meetings", payload, MeetingsInput, run)

    async def extract_meeting_actions(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.meetings.actions import extract_outcome

        async def run(i: MeetingActionsInput) -> Any:
            _window(i.from_date, i.to_date)
            service = self._pick("meetings", i.source)[i.source]
            transcripts = await service.list_transcripts(i.from_date, i.to_date, 50, with_text=True)
            match = next((t for t in transcripts if t.meeting_id == i.meeting_id), None)
            if match is None:
                raise ValueError(
                    f"Meeting {i.meeting_id} not found between {i.from_date} and {i.to_date}"
                )
            outcome = await extract_outcome(match.text, self.reporting_model)
            return {"meeting": match.title, "held_on": str(match.held_on), **outcome.model_dump()}

        return await run_tool("meetings", "extract actions", payload, MeetingActionsInput, run)

    # ---------------------------------------------------------------- chat

    async def post_message(self, payload: dict[str, Any]) -> ToolResult:
        async def run(i: PostMessageInput) -> Any:
            service = self._pick("chat", i.source)[i.source]
            return await service.post_message(i.channel, i.text)

        return await run_tool("chat", "post message", payload, PostMessageInput, run)
