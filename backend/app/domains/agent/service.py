"""Run the agent for a user's question and persist the run."""

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.context import get_request_id
from backend.app.core.errors import AppException, NotFoundError
from backend.app.core.logging import get_logger
from backend.app.core.observability import AGENT_RUN_SECONDS, AGENT_RUNS, span
from backend.app.core.security.permissions import Principal
from backend.app.db.base import utcnow
from backend.app.db.models import AgentRun, RunStatus
from backend.app.domains.agent.deps import AgentDeps
from backend.app.domains.agent.graph import build_agent_graph, recursion_limit
from backend.app.domains.agent.schemas import AgentRunResponse, AgentStep, TokenUsage
from backend.app.domains.agent.state import Observation
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.llm.base import AgentModels
from backend.app.tools.gateway import ToolGateway
from backend.app.tools.registry import ToolRegistry

logger = get_logger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


def _step_event(observation: Observation) -> dict[str, Any]:
    return {
        "type": "step",
        "step": observation.model_dump(include={"step_id", "tool", "purpose", "status", "error",
                                                 "approval_id", "suspicious"}),
    }  # fmt: skip


def run_to_response(run: AgentRun) -> AgentRunResponse:
    steps = [AgentStep.model_validate(step) for step in run.steps]
    return AgentRunResponse(
        run_id=run.id,
        status=run.status,
        query=run.query,
        answer=run.answer,
        citations=run.citations,
        steps=steps,
        pending_approvals=[
            s.approval_id for s in steps if s.status == "pending_approval" and s.approval_id
        ],
        flagged_injection=run.flagged_injection,
        error=run.error,
        usage=TokenUsage(prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens),
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        models: AgentModels,
        registry: ToolRegistry,
    ) -> None:
        self.session = session
        self.settings = settings
        self.models = models
        self.registry = registry

    async def run(self, user: Principal, query: str, on_event: EventSink | None = None) -> AgentRun:
        """Run the agent; ``on_event`` receives progress events (for streaming UIs)."""

        async def emit(event: dict[str, Any]) -> None:
            if on_event is not None:
                await on_event(event)

        started = time.perf_counter()
        with span("agent.run", user_id=user.user_id) as current:
            run = await self._run(user, query, emit)
            current.set_attribute("agent.run_id", str(run.id))
            current.set_attribute("agent.status", run.status.value)
            current.set_attribute("agent.steps", len(run.steps))
            current.set_attribute("llm.tokens", run.prompt_tokens + run.completion_tokens)
        AGENT_RUNS.labels(run.status.value).inc()
        AGENT_RUN_SECONDS.observe(time.perf_counter() - started)
        return run

    async def _run(self, user: Principal, query: str, emit: EventSink) -> AgentRun:

        agent = user.as_agent()
        run = AgentRun(
            id=uuid.uuid4(),
            user_id=user.user_id,
            query=query,
            status=RunStatus.RUNNING,
            operations_model=self.models.operations.model,
            reporting_model=self.models.reporting.model,
            request_id=get_request_id(),
        )
        self.session.add(run)
        await self.session.commit()
        await emit({"type": "run_started", "run_id": str(run.id)})

        deps = AgentDeps(
            models=self.models,
            gateway=ToolGateway(self.registry, self.session, self.settings),
            agent=agent,
            settings=self.settings,
            run_id=run.id,
        )

        # Stream state snapshots so the steps completed so far survive a later failure.
        state: dict = {}
        try:
            async for snapshot in build_agent_graph(deps).astream(
                {"query": query},
                config={"recursion_limit": recursion_limit(deps)},
                stream_mode="values",
            ):
                seen = len(state.get("observations", []))
                state = snapshot
                for observation in state.get("observations", [])[seen:]:
                    await emit(_step_event(observation))
                if "verification" in state and "answer" not in state:
                    await emit({"type": "phase", "phase": "writing_answer"})
            run.answer = state.get("answer")
            run.citations = state.get("citations", [])
            pending = any(o.status == "pending_approval" for o in state.get("observations", []))
            run.status = RunStatus.AWAITING_APPROVAL if pending else RunStatus.COMPLETED
        except AppException as exc:
            logger.warning("Agent run failed", extra={"run_id": str(run.id), "error": exc.message})
            run.status = RunStatus.FAILED
            run.error = exc.message
        except Exception:
            logger.exception("Agent run crashed", extra={"run_id": str(run.id)})
            run.status = RunStatus.FAILED
            run.error = "The agent hit an internal error"

        observations: list[Observation] = state.get("observations", [])
        run.flagged_injection = bool(state.get("risk_flags"))
        run.steps = [o.model_dump(exclude={"content", "arguments"}) for o in observations]
        run.prompt_tokens = deps.usage.prompt_tokens
        run.completion_tokens = deps.usage.completion_tokens
        run.finished_at = utcnow()

        await AuditService(self.session).record(
            actions.AGENT_RUN,
            status=actions.SUCCESS if run.status != RunStatus.FAILED else actions.FAILURE,
            principal=agent,
            resource_type="agent_run",
            resource_id=str(run.id),
            run_id=run.id,
            details={
                "status": run.status.value,
                "steps": [{"tool": o.tool, "status": o.status} for o in observations],
                "flagged_injection": run.flagged_injection,
                "error": run.error,
                "tokens": run.prompt_tokens + run.completion_tokens,
            },
        )
        await self.session.commit()
        return run


async def get_run(session: AsyncSession, run_id: uuid.UUID, viewer: Principal) -> AgentRun:
    run = await session.get(AgentRun, run_id)
    if run is None or run.user_id != viewer.user_id:
        raise NotFoundError("Agent run not found")
    return run


async def list_runs(
    session: AsyncSession, viewer: Principal, *, limit: int, offset: int
) -> tuple[list[AgentRun], int]:
    condition = AgentRun.user_id == viewer.user_id
    total = await session.scalar(select(func.count()).select_from(AgentRun).where(condition))
    runs = await session.scalars(
        select(AgentRun)
        .where(condition)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(runs), total or 0
