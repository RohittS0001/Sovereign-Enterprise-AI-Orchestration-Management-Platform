"""Conditional edges of the agent graph."""

from typing import Literal

from backend.app.orchestration.state.state import AgentState


def route_after_plan(state: AgentState) -> Literal["execute", "plan", "verify"]:
    if state.get("pending"):
        return "execute"
    if state.get("done_planning", False):
        return "verify"
    # The planner only proposed unavailable tools; let it try again with that feedback.
    return "plan"
