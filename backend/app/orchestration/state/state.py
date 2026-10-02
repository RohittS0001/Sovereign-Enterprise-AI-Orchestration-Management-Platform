from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    query: str
    plan: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    verified: bool = False
    final_response: str | None = None