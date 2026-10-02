from backend.app.orchestration.state.state import AgentState


def execute_step(state: AgentState) -> AgentState:
    """
    Execute the current orchestration step.

    Actual capability and MCP execution will be connected later.
    """

    if not state.plan:
        return state

    current_step = state.plan[0]

    state.observations.append(
        {
            "step": current_step,
            "status": "pending_implementation",
        }
    )

    return state