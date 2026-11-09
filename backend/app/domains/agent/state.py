"""State carried through the agent graph."""

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field


class PlannedCall(BaseModel):
    """A tool call as proposed by the planner model."""

    tool: str = Field(description="Exact tool name from the tool list")
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = Field(default="", description="One short sentence: why this call")


class PlannerOutput(BaseModel):
    thought: str = Field(default="", max_length=600, description="Brief reasoning summary")
    done: bool = Field(description="True when no more tool calls are needed")
    steps: list[PlannedCall] = Field(default_factory=list, max_length=10)


class PlanStep(BaseModel):
    id: str  # "S1", "S2", ... referenced by citations
    tool: str
    arguments: dict[str, Any]
    purpose: str


class Observation(BaseModel):
    step_id: str
    tool: str
    purpose: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    content: str = ""
    error: str | None = None
    approval_id: str | None = None
    suspicious: bool = False


class Verification(BaseModel):
    ok: bool
    failed_steps: list[str] = Field(default_factory=list)
    denied_steps: list[str] = Field(default_factory=list)
    pending_approvals: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    query: str
    observations: Annotated[list[Observation], operator.add]
    pending: list[PlanStep]
    iterations: int
    step_counter: int
    done_planning: bool
    risk_flags: list[str]
    verification: Verification
    answer: str
    citations: list[str]
