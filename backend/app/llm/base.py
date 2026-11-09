"""Provider-neutral LLM interface used by the agent."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from backend.app.core.exceptions.exceptions import AppException

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class LLMResponse(BaseModel):
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(AppException):
    """The model server failed or returned something unusable."""

    status_code = 502
    code = "llm_error"


class LLMClient(Protocol):
    model: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return the assistant's reply. ``json_schema`` constrains output to JSON."""
        ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentModels:
    """The two models the platform uses."""

    operations: LLMClient  # LLM 1: planning and tool selection
    reporting: LLMClient  # LLM 2: answers and reports

    async def close(self) -> None:
        await self.operations.close()
        if self.reporting is not self.operations:
            await self.reporting.close()
