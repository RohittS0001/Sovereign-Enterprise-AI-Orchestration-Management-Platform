"""Run the agent for a user's question and persist the run."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit import actions
from backend.app.audit.service import AuditService
from backend.app.core.config.settings import Settings
from backend.app.core.context import get_request_id
from backend.app.core.exceptions.exceptions import AppException, NotFoundError
from backend.app.core.logging.logger import get_logger
from backend.app.core.security.permissions import Principal
from backend.app.db.base import utcnow
from backend.app.db.models import AgentRun, RunStatus
from backend.app.llm.base import AgentModels
from backend.app.mcp.registry.tool_registry import ToolRegistry
from backend.app.orchestration.deps import AgentDeps
from backend.app.orchestration.graph import build_agent_graph, recursion_limit
from backend.app.orchestration.state.state import Observation
from backend.app.orchestration.tool_gateway import ToolGateway
from backend.app.schemas.agent import AgentRunResponse, AgentStep, TokenUsage

logger = get_logger(__name__)


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

    async def run(self, user: Principal, query: str) -> AgentRun:
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
                state = snapshot
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
