"""The agent as a LangGraph state machine.

START -> plan --(steps)--> execute -> plan ... --(done)--> verify -> respond -> END
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from backend.app.agents.reporting.generation.responder import respond
from backend.app.orchestration.deps import AgentDeps
from backend.app.orchestration.execution.executor import execute_pending
from backend.app.orchestration.planner.planner import plan_next
from backend.app.orchestration.routing.router import route_after_plan
from backend.app.orchestration.state.state import AgentState
from backend.app.orchestration.verification.verifier import verify_run


def build_agent_graph(deps: AgentDeps) -> CompiledStateGraph:
    async def plan(state: AgentState) -> AgentState:
        return await plan_next(state, deps)

    async def execute(state: AgentState) -> AgentState:
        return await execute_pending(state, deps)

    async def answer(state: AgentState) -> AgentState:
        return await respond(state, deps)

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan)
    graph.add_node("execute", execute)
    graph.add_node("verify", verify_run)
    graph.add_node("respond", answer)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", route_after_plan, {"execute": "execute", "plan": "plan", "verify": "verify"}
    )
    graph.add_edge("execute", "plan")
    graph.add_edge("verify", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def recursion_limit(deps: AgentDeps) -> int:
    # plan + execute per iteration, plus verify/respond and some slack.
    return deps.settings.agent_max_iterations * 2 + 6
