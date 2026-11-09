"""Ask a model for JSON matching a Pydantic model, with one self-correction retry."""

import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from backend.app.core.logging import get_logger
from backend.app.llm.base import ChatMessage, LLMClient, LLMError

logger = get_logger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMOutputError(LLMError):
    code = "llm_output_invalid"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion


def _strip_fences(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


async def generate_structured[T: BaseModel](
    client: LLMClient,
    messages: list[ChatMessage],
    output_model: type[T],
    *,
    usage: Usage | None = None,
    retries: int = 1,
) -> T:
    schema = output_model.model_json_schema()
    conversation = list(messages)
    last_error = ""

    for attempt in range(retries + 1):
        response = await client.chat(conversation, json_schema=schema)
        if usage is not None:
            usage.add(response.prompt_tokens, response.completion_tokens)

        try:
            return output_model.model_validate_json(_strip_fences(response.content))
        except ValidationError as exc:
            last_error = str(exc)[:1000]
            logger.warning(
                "Model returned invalid structured output",
                extra={"model": client.model, "attempt": attempt + 1},
            )
            conversation = [
                *conversation,
                ChatMessage("assistant", response.content[:4000]),
                ChatMessage(
                    "user",
                    "That output did not match the required JSON schema:\n"
                    f"{last_error}\nReply again with only valid JSON matching the schema.",
                ),
            ]

    raise LLMOutputError(f"Model output did not match {output_model.__name__}: {last_error}")
