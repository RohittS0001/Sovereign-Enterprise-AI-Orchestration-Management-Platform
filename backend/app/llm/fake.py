"""Deterministic LLM for tests and offline evaluation."""

import json
from collections.abc import Callable
from typing import Any

from backend.app.llm.base import ChatMessage, LLMError, LLMResponse

Responder = Callable[[list[ChatMessage], dict[str, Any] | None], str | dict[str, Any]]


class FakeLLM:
    """Returns scripted replies.

    ``replies`` may be a list (consumed in order) or a function of
    ``(messages, json_schema)``. Dict replies are serialized to JSON. Every call is
    recorded in ``calls`` so tests can assert on prompts.
    """

    def __init__(self, replies: list[str | dict[str, Any]] | Responder, model: str = "fake"):
        self.model = model
        self._replies = replies
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "json_schema": json_schema})

        if callable(self._replies):
            reply = self._replies(messages, json_schema)
        else:
            if not self._replies:
                raise LLMError("FakeLLM has no scripted replies left")
            reply = self._replies.pop(0)

        content = json.dumps(reply) if isinstance(reply, dict) else reply
        return LLMResponse(
            content=content,
            model=self.model,
            prompt_tokens=sum(len(m.content) for m in messages) // 4,
            completion_tokens=len(content) // 4,
        )

    async def close(self) -> None:
        return None
