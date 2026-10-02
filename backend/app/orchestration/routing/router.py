from backend.app.orchestration.state.state import AgentState


def route_next_step(state: AgentState) -> AgentState:
    """
    Determine the next orchestration step from the current plan.
    """

    if not state.plan:
        return state

    current_step = state.plan[0]

    if current_step == "understand_request":
        state.findings.append("Request understanding required.")

    elif current_step == "identify_required_information":
        state.findings.append("Required information identification required.")

    elif current_step == "select_capabilities":
        state.findings.append("Capability selection required.")

    elif current_step == "execute_actions":
        state.findings.append("Action execution required.")

    elif current_step == "verify_results":
        state.findings.append("Result verification required.")

    return state