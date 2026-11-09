from backend.app.core.config.settings import Settings
from backend.app.llm.base import AgentModels, LLMClient
from backend.app.llm.http_clients import OllamaClient, OpenAICompatibleClient


def _client(settings: Settings, model: str) -> LLMClient:
    cls = OllamaClient if settings.llm_provider == "ollama" else OpenAICompatibleClient
    return cls(
        base_url=settings.llm_base_url,
        model=model,
        timeout=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        api_key=settings.llm_api_key.get_secret_value(),
    )


def build_agent_models(settings: Settings) -> AgentModels | None:
    """Create the operations and reporting models, or ``None`` if none is configured."""
    if not settings.llm_configured:
        return None

    operations = _client(settings, settings.llm1_model_name)
    if not settings.llm2_model_name or settings.llm2_model_name == settings.llm1_model_name:
        return AgentModels(operations=operations, reporting=operations)
    return AgentModels(operations=operations, reporting=_client(settings, settings.llm2_model_name))
