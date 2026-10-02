from backend.app.orchestration.execution.executor import execute_step
from backend.app.orchestration.planner.planner import create_plan
from backend.app.orchestration.routing.router import route_next_step
from backend.app.orchestration.state.state import AgentState
from backend.app.orchestration.verification.verifier import verify_results


def test_agent_state_initialization() -> None:
    state = AgentState(query="Why is Project Phoenix at risk?")

    assert state.query == "Why is Project Phoenix at risk?"
    assert state.plan == []
    assert state.tool_calls == []
    assert state.observations == []
    assert state.findings == []
    assert state.verified is False
    assert state.final_response is None


def test_planner_creates_plan() -> None:
    state = AgentState(query="Why is Project Phoenix at risk?")

    state = create_plan(state)

    assert state.plan
    assert state.plan == [
        "understand_request",
        "identify_required_information",
        "select_capabilities",
        "execute_actions",
        "verify_results",
    ]


def test_router_processes_current_step() -> None:
    state = AgentState(query="Why is Project Phoenix at risk?")
    state = create_plan(state)

    state = route_next_step(state)

    assert state.findings
    assert "Request understanding required." in state.findings


def test_executor_records_observation() -> None:
    state = AgentState(query="Why is Project Phoenix at risk?")
    state = create_plan(state)

    state = execute_step(state)

    assert state.observations
    assert state.observations[0] == {
        "step": "understand_request",
        "status": "pending_implementation",
    }


def test_verifier_verifies_successful_observations() -> None:
    state = AgentState(query="Why is Project Phoenix at risk?")
    state = create_plan(state)
    state = execute_step(state)

    state = verify_results(state)

    assert state.verified is True


def test_verifier_rejects_failed_observation() -> None:
    state = AgentState(query="Why is Project Phoenix at risk?")

    state.observations.append(
        {
            "step": "get_project",
            "status": "failed",
        }
    )

    state = verify_results(state)

    assert state.verified is False