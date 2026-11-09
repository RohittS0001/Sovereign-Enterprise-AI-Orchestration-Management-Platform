"""Plan node: LLM 1 decides the next tool calls from the question and results so far."""

import json

from backend.app.agents.operations.prompts.planner import (
    PLANNER_SYSTEM,
    PLANNER_USER,
    render_tools,
)
from backend.app.core.logging.logger import get_logger
from backend.app.guardrails.redaction import redact_text
from backend.app.llm.base import ChatMessage
from backend.app.llm.structured import generate_structured
from backend.app.orchestration.deps import AgentDeps
from backend.app.orchestration.observations import render_observations
from backend.app.orchestration.state.state import (
    AgentState,
    Observation,
    PlannerOutput,
    PlanStep,
)

logger = get_logger(__name__)


def _call_key(tool: str, arguments: dict) -> str:
    return f"{tool}:{json.dumps(arguments, sort_keys=True, default=str)}"


async def plan_next(state: AgentState, deps: AgentDeps) -> AgentState:
    settings = deps.settings
    iterations = state.get("iterations", 0)
    step_counter = state.get("step_counter", 0)
    observations = state.get("observations", [])
    remaining = settings.agent_max_steps - step_counter

    if iterations >= settings.agent_max_iterations or remaining <= 0:
        logger.info("Planning budget exhausted", extra={"run_id": str(deps.run_id)})
        return {"pending": [], "done_planning": True}

    specs = deps.gateway.available_tools(deps.agent)
    if not specs:
        return {"pending": [], "done_planning": True, "iterations": iterations + 1}

    history = ""
    if observations:
        history = "Results of earlier tool calls:\n" + render_observations(observations) + "\n"

    query = redact_text(
        state["query"], include_contact_info=settings.guardrails_redact_contact_info
    ).text
    messages = [
        ChatMessage(
            "system",
            PLANNER_SYSTEM.format(today=deps.today.isoformat(), tools=render_tools(specs)),
        ),
        ChatMessage("user", PLANNER_USER.format(query=query, history=history, remaining=remaining)),
    ]
    output = await generate_structured(
        deps.models.operations, messages, PlannerOutput, usage=deps.usage
    )

    available = {spec.name for spec in specs}
    already_done = {_call_key(o.tool, o.arguments) for o in observations if o.status == "success"}
    pending: list[PlanStep] = []
    rejected: list[Observation] = []

    for call in output.steps[:remaining]:
        step_id = f"S{step_counter + len(pending) + len(rejected) + 1}"
        if call.tool not in available:
            # Shown to the planner next round so it can correct itself.
            rejected.append(
                Observation(
                    step_id=step_id,
                    tool=call.tool,
                    purpose=call.purpose,
                    arguments=call.arguments,
                    status="error",
                    error="Tool does not exist or is not available to this user",
                )
            )
            continue
        if _call_key(call.tool, call.arguments) in already_done:
            continue
        pending.append(
            PlanStep(id=step_id, tool=call.tool, arguments=call.arguments, purpose=call.purpose)
        )

    done = (output.done and not pending) or (not pending and not rejected)
    logger.info(
        "Planned steps",
        extra={
            "run_id": str(deps.run_id),
            "iteration": iterations + 1,
            "steps": [s.tool for s in pending],
            "done": done,
        },
    )
    return {
        "pending": pending,
        "observations": rejected,
        "iterations": iterations + 1,
        "step_counter": step_counter + len(pending) + len(rejected),
        "done_planning": done,
    }
