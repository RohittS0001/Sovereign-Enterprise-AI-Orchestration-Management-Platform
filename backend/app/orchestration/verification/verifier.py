from backend.app.orchestration.state.state import AgentState


def verify_results(state: AgentState) -> AgentState:
    """
    Verify the results produced during the current orchestration run.

    Detailed verification logic will be added later.
    """

    if not state.observations:
        state.verified = False
        return state

    state.verified = all(
        observation.get("status") != "failed"
        for observation in state.observations
    )

    return state