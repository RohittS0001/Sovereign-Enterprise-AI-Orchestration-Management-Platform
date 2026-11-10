import asyncio
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from backend.app.core.errors import AppException
from backend.app.db.models import Project
from backend.app.db.session import Database
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.negotiation import negotiate
from backend.app.domains.analytics.service import get_project, project_health
from backend.app.domains.analytics.twin import Scenario, simulate
from backend.app.integrations.jira.validators import ProjectKey
from backend.app.tools.base import ToolResult, ToolStatus


class ProjectHealthInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")


class ListProjectsInput(BaseModel):
    pass


class SimulateDeliveryInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")
    target_date: date | None = Field(default=None, description="Default: the sprint end")
    add_people: int = Field(default=0, ge=0, le=10, description="What-if: extra engineers")
    cut_points: float = Field(default=0, ge=0, description="What-if: lowest-priority points cut")
    extra_leave_days: int = Field(default=0, ge=0, le=60, description="What-if: lost days")
    resolve_blockers: bool = Field(default=False, description="What-if: blockers clear today")


class WhoKnowsInput(BaseModel):
    topic: str = Field(min_length=2, max_length=200, description="e.g. 'refunds service'")


class AbsenceInput(BaseModel):
    person: str = Field(min_length=1, max_length=200, description="Person's name as in Jira")


class NegotiationInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")
    target_date: date | None = Field(default=None, description="Default: the sprint end")


class DoraInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")
    window_days: int = Field(default=30, ge=7, le=180, description="Look-back window in days")


class ReleaseNotesInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")
    since: date | None = Field(default=None, description="Default: the sprint start")
    until: date | None = Field(default=None, description="Default: today")


class EstimateInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")
    title: str = Field(min_length=3, max_length=500, description="The story to estimate")


class StatementInput(BaseModel):
    project_key: ProjectKey = Field(description="Jira project key, e.g. PHX")
    statement: str = Field(min_length=3, max_length=2000, description="The status claim to check")


class AnalyticsTools:
    """Computed, evidence-backed project analytics for the agent."""

    def __init__(self, database: Database, collector: SnapshotCollector) -> None:
        self.database = database
        self.collector = collector

    async def list_projects(self, payload: dict[str, Any]) -> ToolResult:
        async with self.database.session() as session:
            projects = await session.scalars(select(Project).where(Project.is_active.is_(True)))
            data = [{"key": p.key, "name": p.name, "repos": p.github_repos} for p in projects]
        return ToolResult(status=ToolStatus.SUCCESS, data={"projects": data})

    async def project_health(self, payload: dict[str, Any]) -> ToolResult:
        try:
            inputs = ProjectHealthInput.model_validate(payload)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        try:
            async with self.database.session() as session:
                health = await project_health(session, self.collector, inputs.project_key)
        except AppException as exc:
            return ToolResult(status=ToolStatus.ERROR, error=exc.message)
        # Drop zero-score factors and per-issue lists the model does not need.
        data = health.model_dump(mode="json")
        data["risk"]["factors"] = [f for f in data["risk"]["factors"] if f["score"] > 0]
        return ToolResult(status=ToolStatus.SUCCESS, data=data)

    async def _snapshot(self, key: str):
        async with self.database.session() as session:
            project = await get_project(session, key)
        return await self.collector.collect(project)

    async def simulate_delivery(self, payload: dict[str, Any]) -> ToolResult:
        try:
            i = SimulateDeliveryInput.model_validate(payload)
            snapshot = await self._snapshot(i.project_key)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        except AppException as exc:
            return ToolResult(status=ToolStatus.ERROR, error=exc.message)
        scenario = Scenario(
            name="what_if"
            if (i.add_people or i.cut_points or i.extra_leave_days or i.resolve_blockers)
            else "baseline",
            target_date=i.target_date,
            add_people=i.add_people,
            cut_points=i.cut_points,
            extra_leave_days=i.extra_leave_days,
            resolve_blockers=i.resolve_blockers,
        )
        result = await asyncio.to_thread(simulate, snapshot, scenario)
        data = result.model_dump(mode="json", exclude={"histogram"})
        return ToolResult(status=ToolStatus.SUCCESS, data=data)

    async def negotiation_options(self, payload: dict[str, Any]) -> ToolResult:
        try:
            i = NegotiationInput.model_validate(payload)
            snapshot = await self._snapshot(i.project_key)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        except AppException as exc:
            return ToolResult(status=ToolStatus.ERROR, error=exc.message)
        plan = await negotiate(snapshot, i.target_date)
        data = plan.model_dump(mode="json")
        for item in [data["baseline"], *(o["result"] for o in data["options"])]:
            item.pop("histogram", None)
        return ToolResult(status=ToolStatus.SUCCESS, data=data)

    async def _graph(self):
        from backend.app.domains.graph.service import build_graph

        async with self.database.session() as session:
            return await build_graph(session, self.collector)

    async def who_knows(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.graph.service import who_knows

        try:
            i = WhoKnowsInput.model_validate(payload)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        experts = who_knows(await self._graph(), i.topic)
        return ToolResult(
            status=ToolStatus.SUCCESS, data={"experts": [e.model_dump() for e in experts]}
        )

    async def impact_of_absence(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.graph.service import impact_of_absence

        try:
            i = AbsenceInput.model_validate(payload)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        return ToolResult(
            status=ToolStatus.SUCCESS,
            data=impact_of_absence(await self._graph(), i.person).model_dump(),
        )

    async def predict_slip(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.analytics.predictive import predict_slip

        try:
            i = ProjectHealthInput.model_validate(payload)
            async with self.database.session() as session:
                health = await project_health(session, self.collector, i.project_key)
                prediction = await predict_slip(session, health)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        except AppException as exc:
            return ToolResult(status=ToolStatus.ERROR, error=exc.message)
        return ToolResult(status=ToolStatus.SUCCESS, data=prediction.model_dump(mode="json"))

    # ------------------------------------------------------------ delivery lifecycle

    async def _delivery(
        self, model: type[BaseModel], payload: dict[str, Any], compute
    ) -> ToolResult:
        try:
            inputs = model.model_validate(payload)
            snapshot = await self._snapshot(inputs.project_key)
            data = await compute(inputs, snapshot)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid input: {exc}")
        except AppException as exc:
            return ToolResult(status=ToolStatus.ERROR, error=exc.message)
        return ToolResult(status=ToolStatus.SUCCESS, data=data)

    async def critical_path(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.roadmap import build_roadmap

        async def compute(_, snapshot):
            roadmap = build_roadmap(snapshot).model_dump(mode="json")
            for item in roadmap["items"]:  # keep what the model needs
                for name in ("start_day", "finish_day", "after"):
                    item.pop(name, None)
            return roadmap

        return await self._delivery(ProjectHealthInput, payload, compute)

    async def dora_metrics(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.dora import dora_metrics, incident_history

        async def compute(i, snapshot):
            incidents = await incident_history(self.collector.hub, snapshot)
            return dora_metrics(snapshot, incidents, window_days=i.window_days).model_dump(
                mode="json"
            )

        return await self._delivery(DoraInput, payload, compute)

    async def release_notes(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.release import release_notes

        async def compute(i, snapshot):
            notes = release_notes(snapshot, i.since, i.until)
            return notes.model_dump(mode="json", exclude={"sections"})

        return await self._delivery(ReleaseNotesInput, payload, compute)

    async def retrospective(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.release import retrospective

        async def compute(i, snapshot):
            async with self.database.session() as session:
                health = await project_health(session, self.collector, i.project_key, record=False)
            return (await retrospective(snapshot, health)).model_dump(mode="json")

        return await self._delivery(ProjectHealthInput, payload, compute)

    async def estimate_story(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.backlog import estimate

        async def compute(i, snapshot):
            points, basis, similar = estimate(i.title, snapshot.issues)
            return {"points": points, "basis": basis, "similar_issues": similar}

        return await self._delivery(EstimateInput, payload, compute)

    async def _delay_inputs(self, snapshot):
        from backend.app.core.context import principal_var
        from backend.app.core.security.permissions import Permission
        from backend.app.domains.delivery.delay import collect_delay_facts
        from backend.app.domains.delivery.roadmap import build_roadmap

        principal = principal_var.get(None)
        include_email = bool(principal and principal.has(Permission.EMAIL_READ))
        facts = await collect_delay_facts(self.collector.hub, snapshot, include_email=include_email)
        return facts, build_roadmap(snapshot)

    async def delay_analysis(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.delay import analyze_delay

        async def compute(_, snapshot):
            facts, road = await self._delay_inputs(snapshot)
            report = analyze_delay(snapshot, facts, road).model_dump(mode="json")
            report["stories"] = [s for s in report["stories"] if s["days_lost"] > 0][:6]
            return report

        return await self._delivery(ProjectHealthInput, payload, compute)

    async def check_statement(self, payload: dict[str, Any]) -> ToolResult:
        from backend.app.domains.delivery.delay import check_statement

        async def compute(i, snapshot):
            facts, road = await self._delay_inputs(snapshot)
            checks = check_statement(i.statement, snapshot, facts, road)
            return {"checks": [c.model_dump(mode="json") for c in checks]}

        return await self._delivery(StatementInput, payload, compute)
