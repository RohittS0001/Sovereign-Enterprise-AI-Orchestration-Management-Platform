import uuid
from dataclasses import dataclass, field
from datetime import date

from backend.app.core.config.settings import Settings
from backend.app.core.security.permissions import Principal
from backend.app.llm.base import AgentModels
from backend.app.llm.structured import Usage
from backend.app.orchestration.tool_gateway import ToolGateway


@dataclass
class AgentDeps:
    """Everything the graph nodes need for one run."""

    models: AgentModels
    gateway: ToolGateway
    agent: Principal  # the agent acting on behalf of the user
    settings: Settings
    run_id: uuid.UUID
    usage: Usage = field(default_factory=Usage)
    today: date = field(default_factory=date.today)
