"""HTTP clients for locally hosted models: Ollama and OpenAI-compatible servers (vLLM)."""

import asyncio
import time
from typing import Any

import httpx

from backend.app.core.logging import get_logger
from backend.app.core.observability import LLM_SECONDS, LLM_TOKENS, span
from backend.app.llm.base import ChatMessage, LLMError, LLMResponse

logger = get_logger(__name__)


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class _HttpLLM:
    provider = "llm"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float,
        temperature: float = 0.0,
        api_key: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        chat_path: str | None = None,
        retries: int = 2,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.chat_path = chat_path
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    def _record(self, prompt_tokens: int, completion_tokens: int, started: float) -> None:
        LLM_SECONDS.labels(self.model).observe(time.perf_counter() - started)
        LLM_TOKENS.labels(self.model, "prompt").inc(prompt_tokens)
        LLM_TOKENS.labels(self.model, "completion").inc(completion_tokens)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        attributes = {"openinference.span.kind": "LLM", "llm.model_name": self.model,
                      "llm.provider": self.provider}  # fmt: skip
        with span("llm.request", provider=self.provider, model=self.model, **attributes):
            return await self._post_unspanned(path, payload)

    async def _post_unspanned(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Hosted and free-tier models return 429/5xx under load; retry those with backoff.
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                response = await self._client.post(path, json=payload)
            except httpx.TimeoutException as exc:
                if not last:
                    await asyncio.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                raise LLMError(f"{self.provider} request timed out (model {self.model})") from exc
            except httpx.RequestError as exc:
                raise LLMError(
                    f"Could not reach {self.provider} at {self._client.base_url}: "
                    f"{exc.__class__.__name__}"
                ) from exc
            if response.status_code in RETRYABLE_STATUS and not last:
                logger.info(
                    "LLM busy; retrying",
                    extra={"provider": self.provider, "upstream_status": response.status_code},
                )
                await asyncio.sleep(self.backoff_seconds * (attempt + 1))
                continue
            break

        if response.is_error:
            logger.warning(
                "LLM request failed",
                extra={"provider": self.provider, "upstream_status": response.status_code},
            )
            raise LLMError(
                f"{self.provider} returned {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(f"{self.provider} returned a non-JSON response") from exc

    async def close(self) -> None:
        await self._client.aclose()


class OllamaClient(_HttpLLM):
    """Ollama ``/api/chat``. Structured output uses Ollama's ``format`` JSON schema."""

    provider = "ollama"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        options: dict[str, Any] = {
            "temperature": self.temperature if temperature is None else temperature
        }
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": False,
            "options": options,
        }
        if json_schema is not None:
            payload["format"] = json_schema

        started = time.perf_counter()
        body = await self._post("/api/chat", payload)
        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError("Ollama response is missing message content") from exc

        response = LLMResponse(
            content=content,
            model=body.get("model", self.model),
            prompt_tokens=body.get("prompt_eval_count") or 0,
            completion_tokens=body.get("eval_count") or 0,
        )
        self._record(response.prompt_tokens, response.completion_tokens, started)
        return response


class OpenAICompatibleClient(_HttpLLM):
    """``/v1/chat/completions`` as served by vLLM, llama.cpp server, LM Studio, etc."""

    provider = "openai_compatible"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema},
            }

        started = time.perf_counter()
        body = await self._post(self.chat_path or "/v1/chat/completions", payload)
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Model server response is missing choices") from exc

        usage = body.get("usage") or {}
        response = LLMResponse(
            content=content,
            model=body.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
        )
        self._record(response.prompt_tokens, response.completion_tokens, started)
        return response
