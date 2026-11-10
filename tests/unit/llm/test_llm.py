import json

import httpx
import pytest
from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.llm.base import ChatMessage, LLMError
from backend.app.llm.factory import build_agent_models
from backend.app.llm.fake import FakeLLM
from backend.app.llm.http_clients import OllamaClient, OpenAICompatibleClient
from backend.app.llm.structured import LLMOutputError, Usage, generate_structured

MESSAGES = [ChatMessage("system", "be brief"), ChatMessage("user", "hi")]
SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}


def ollama(handler) -> OllamaClient:
    return OllamaClient(
        base_url="http://ollama:11434/",
        model="qwen3:8b",
        timeout=5,
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )


async def test_ollama_sends_schema_and_parses_reply() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {"role": "assistant", "content": '{"x": 1}'},
                "prompt_eval_count": 12,
                "eval_count": 3,
            },
        )

    client = ollama(handler)
    response = await client.chat(MESSAGES, json_schema=SCHEMA, max_tokens=50)
    await client.close()

    assert seen["path"] == "/api/chat"
    assert seen["body"]["format"] == SCHEMA
    assert seen["body"]["stream"] is False
    assert seen["body"]["options"] == {"temperature": 0.0, "num_predict": 50}
    assert seen["body"]["messages"][1] == {"role": "user", "content": "hi"}
    assert response.content == '{"x": 1}'
    assert (response.prompt_tokens, response.completion_tokens) == (12, 3)


async def test_openai_compatible_payload_and_auth() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "llama",
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )

    client = OpenAICompatibleClient(
        base_url="http://vllm:8000",
        model="llama",
        timeout=5,
        api_key="internal-key",
        transport=httpx.MockTransport(handler),
    )
    response = await client.chat(MESSAGES, json_schema=SCHEMA)

    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer internal-key"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert response.content == "hello"
    assert response.prompt_tokens == 5


@pytest.mark.parametrize(
    ("handler", "match"),
    [
        (lambda r: httpx.Response(500, text="model not found"), "returned 500"),
        (lambda r: httpx.Response(200, text="not json"), "non-JSON"),
        (lambda r: httpx.Response(200, json={"unexpected": True}), "missing message"),
    ],
)
async def test_ollama_errors_become_llm_errors(handler, match) -> None:
    with pytest.raises(LLMError, match=match):
        await ollama(handler).chat(MESSAGES)


async def test_unreachable_server_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError, match="Could not reach ollama"):
        await ollama(handler).chat(MESSAGES)


def test_factory_returns_none_without_model() -> None:
    assert build_agent_models(Settings(_env_file=None, llm1_model_name="")) is None


def test_factory_shares_one_client_when_models_match() -> None:
    settings = Settings(_env_file=None, llm1_model_name="qwen3:8b", llm2_model_name="")
    models = build_agent_models(settings)

    assert models.operations is models.reporting
    assert isinstance(models.operations, OllamaClient)


def test_factory_builds_separate_reporting_model() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_base_url="http://vllm:8000",
        llm1_model_name="ops-model",
        llm2_model_name="report-model",
    )
    models = build_agent_models(settings)

    assert isinstance(models.reporting, OpenAICompatibleClient)
    assert (models.operations.model, models.reporting.model) == ("ops-model", "report-model")


class Answer(BaseModel):
    value: int


async def test_generate_structured_strips_code_fences() -> None:
    usage = Usage()
    result = await generate_structured(
        FakeLLM(['```json\n{"value": 3}\n```']), MESSAGES, Answer, usage=usage
    )

    assert result.value == 3
    assert usage.completion_tokens > 0


async def test_generate_structured_retries_with_feedback() -> None:
    llm = FakeLLM(['{"value": "three"}', {"value": 3}])

    result = await generate_structured(llm, MESSAGES, Answer)

    assert result.value == 3
    assert len(llm.calls) == 2
    feedback = llm.calls[1]["messages"][-1].content
    assert "did not match the required JSON schema" in feedback
    assert llm.calls[0]["json_schema"] == Answer.model_json_schema()


async def test_generate_structured_gives_up() -> None:
    with pytest.raises(LLMOutputError, match="Answer"):
        await generate_structured(FakeLLM(["nope", "still nope"]), MESSAGES, Answer)


async def test_busy_hosted_models_are_retried_then_succeed() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) < 3:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = OpenAICompatibleClient(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai", model="gemini",
        timeout=5, api_key="k", transport=httpx.MockTransport(handler),
        chat_path="/chat/completions", retries=2, backoff_seconds=0,
    )  # fmt: skip

    response = await client.chat(MESSAGES)

    assert response.content == "OK"
    assert calls == ["/v1beta/openai/chat/completions"] * 3  # Gemini's path, no extra /v1


async def test_retries_are_bounded() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, text="quota")

    client = OpenAICompatibleClient(base_url="http://x", model="m", timeout=5, retries=1,
                                    backoff_seconds=0, transport=httpx.MockTransport(handler))  # fmt: skip
    with pytest.raises(LLMError, match="429"):
        await client.chat(MESSAGES)
    assert len(calls) == 2


def test_factory_passes_the_chat_path_and_retries() -> None:
    from backend.app.core.config import Settings
    from backend.app.llm.factory import build_agent_models

    settings = Settings(
        _env_file=None, environment="test", llm_provider="openai_compatible",
        llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        llm_chat_path="/chat/completions", llm1_model_name="gemini-3.8-flash", llm_retries=3,
    )  # fmt: skip
    models = build_agent_models(settings)
    assert models.operations.chat_path == "/chat/completions" and models.operations.retries == 3
