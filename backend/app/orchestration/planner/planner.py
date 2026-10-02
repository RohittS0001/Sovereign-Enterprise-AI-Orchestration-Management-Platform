from backend.app.orchestration.state.state import AgentState


def create_plan(state: AgentState) -> AgentState:
    """
    Create a plan for the current agent request.

    The intelligent planning logic will be added later.
    For now, this function defines the planner's interface
    within the orchestration workflow.
    """

    if not state.query.strip():
        return state

    state.plan = [
        "understand_request",
        "identify_required_information",
        "select_capabilities",
        "execute_actions",
        "verify_results",
    ]

    return state