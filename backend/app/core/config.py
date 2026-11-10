"""Typed application settings loaded from environment variables and ``.env``."""

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from backend.app.core.connector_settings import ConnectorSettings

PROJECT_ROOT = Path(__file__).resolve().parents[4]

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "text"]


class Settings(ConnectorSettings):
    # Application
    app_name: str = "Sovereign Enterprise AI Orchestration & Management Platform"
    version: str = "0.1.0"
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
    log_stream: Literal["stdout", "stderr"] = "stdout"

    # Backend
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Database
    database_url: str = ""
    database_echo: bool = False

    # Security / auth
    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "sovereign-platform"
    jwt_audience: str = "sovereign-api"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=24 * 60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=90)
    login_rate_limit_attempts: int = Field(default=10, ge=1)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1)
    agent_rate_limit_per_minute: int = Field(default=30, ge=1)

    # Local LLMs (served by Ollama or any OpenAI-compatible server such as vLLM)
    llm_provider: Literal["ollama", "openai_compatible"] = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: SecretStr = SecretStr("")
    # Path of the chat endpoint for openai_compatible servers. Gemini's OpenAI-compatible API
    # is https://generativelanguage.googleapis.com/v1beta/openai + /chat/completions.
    llm_chat_path: str = "/v1/chat/completions"
    llm_retries: int = Field(default=2, ge=0, le=5)  # on 429/5xx/timeouts, with backoff
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    # LLM 1 plans and calls tools; LLM 2 writes answers and reports (falls back to LLM 1).
    llm1_model_path: str = ""
    llm1_model_name: str = ""
    llm2_model_path: str = ""
    llm2_model_name: str = ""

    # Embeddings for document search. "auto" = the LLM server when EMBEDDING_MODEL_NAME is
    # set (e.g. nomic-embed-text, bge-m3), otherwise the offline hashing embedder.
    embedding_provider: Literal["auto", "ollama", "openai_compatible", "hashing", "none"] = "auto"
    embedding_model_name: str = ""
    embedding_base_url: str = ""  # empty = LLM_BASE_URL
    embedding_dimensions: int = Field(default=384, ge=16, le=4096)  # hashing embedder only

    # Retrieval (RAG)
    rag_chunk_size: int = Field(default=1200, ge=200, le=8000)  # characters
    rag_chunk_overlap: int = Field(default=150, ge=0, le=2000)
    rag_top_k: int = Field(default=6, ge=1, le=50)
    rag_candidate_k: int = Field(default=40, ge=5, le=500)
    rag_max_chunks_per_document: int = Field(default=3, ge=1, le=50)
    rag_max_upload_mb: int = Field(default=10, ge=1, le=200)
    # Optional local reranker speaking the text-embeddings-inference /rerank API.
    reranker_url: str = ""

    # Agent loop limits
    agent_max_iterations: int = Field(default=4, ge=1, le=20)
    agent_max_steps: int = Field(default=10, ge=1, le=50)
    agent_observation_max_chars: int = Field(default=6000, ge=500)

    # Tool execution
    tool_timeout_seconds: float = Field(default=30.0, gt=0)
    tool_read_retries: int = Field(default=2, ge=0, le=5)
    tool_retry_backoff_seconds: float = Field(default=0.5, ge=0)

    # Human approval of agent write actions
    approval_ttl_hours: int = Field(default=72, ge=1)
    # False enforces "four eyes": someone other than the requester must approve.
    approval_allow_self_approval: bool = True
    # Write tools the agent may run without approval (e.g. ["jira.add_comment"]).
    approval_exempt_tools: list[str] = Field(default_factory=list)

    # Guardrails
    # Secrets, card numbers and government IDs are always masked before reaching the
    # LLM or the audit log. Set this to also mask email addresses and phone numbers.
    guardrails_redact_contact_info: bool = False

    # Jira
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_timeout_seconds: float = Field(default=30.0, gt=0)
    # Jira Cloud stores story points in a custom field; check yours in the issue JSON.
    jira_story_points_field: str = "customfield_10016"

    # Gmail
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = SecretStr("")
    gmail_refresh_token: SecretStr = SecretStr("")
    gmail_redirect_uri: str = ""
    gmail_credentials_file: Path = PROJECT_ROOT / "credentials.json"
    gmail_token_file: Path = PROJECT_ROOT / "token.json"
    # Drafts may only be addressed to these domains (empty = any).
    gmail_allowed_recipient_domains: list[str] = Field(default_factory=list)

    # Keka (HRMS). Base URL looks like https://<company>.keka.com/api/v1
    keka_base_url: str = ""
    keka_token_url: str = "https://login.keka.com/connect/token"  # noqa: S105 (URL)
    keka_client_id: str = ""
    keka_client_secret: SecretStr = SecretStr("")
    keka_api_key: SecretStr = SecretStr("")
    keka_timeout_seconds: float = Field(default=30.0, gt=0)

    # GitHub (or GitHub Enterprise: https://<host>/api/v3)
    github_token: SecretStr = SecretStr("")
    github_api_url: str = "https://api.github.com"
    # "owner/repo" entries the agent may access (empty = anything the token can see).
    github_allowed_repos: list[str] = Field(default_factory=list)
    github_timeout_seconds: float = Field(default=30.0, gt=0)

    # Demo mode: every integration is served from a built-in synthetic dataset.
    demo_mode: bool = False

    # Analytics & reports
    analytics_cache_seconds: int = Field(default=60, ge=0)
    analytics_default_capacity_points: float = Field(default=10.0, gt=0)
    report_schedule_enabled: bool = False
    report_schedule_weekday: int = Field(default=0, ge=0, le=6)  # 0 = Monday
    report_schedule_hour_utc: int = Field(default=3, ge=0, le=23)  # 03:00 UTC = 08:30 IST

    # Usage limits (0 = unlimited)
    agent_daily_token_quota: int = Field(default=0, ge=0)

    # Observability
    metrics_enabled: bool = True
    metrics_token: SecretStr = SecretStr("")  # if set, /metrics requires "Bearer <token>"
    otel_exporter_otlp_endpoint: str = ""  # e.g. http://localhost:4318 (Jaeger, Tempo, collector)
    otel_service_name: str = "sovereign-api"

    # MCP server (code mode) at /mcp and over stdio; clients use personal access tokens.
    mcp_enabled: bool = True
    mcp_rate_limit_per_minute: int = Field(default=30, ge=1)
    mcp_code_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    mcp_max_steps: int = Field(default=200_000, ge=1_000)
    mcp_max_tool_calls: int = Field(default=50, ge=1, le=500)
    mcp_max_output_chars: int = Field(default=20_000, ge=1_000)
    mcp_token_max_days: int = Field(default=90, ge=1, le=365)

    _jwt_secret_generated: bool = PrivateAttr(default=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("environment", "log_format", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("jira_base_url")
    @classmethod
    def _validate_jira_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("JIRA_BASE_URL must start with http:// or https://")
        return value

    @field_validator("gmail_credentials_file", "gmail_token_file")
    @classmethod
    def _resolve_from_project_root(cls, value: Path) -> Path:
        # Relative paths in .env are relative to the project, not the working directory.
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("llm_chat_path")
    @classmethod
    def _chat_path(cls, value: str) -> str:
        # Catches e.g. Git Bash rewriting "/chat/completions" into "C:/Program Files/Git/...".
        if not value.startswith("/") or ":" in value or " " in value:
            raise ValueError("LLM_CHAT_PATH must be a URL path such as /v1/chat/completions")
        return value

    @field_validator("google_service_account_file")
    @classmethod
    def _resolve_optional_path(cls, value: Path | None) -> Path | None:
        if value is None or str(value) in ("", "."):
            return None
        return value if value.is_absolute() else PROJECT_ROOT / value

    @model_validator(mode="after")
    def _ensure_jwt_secret(self) -> Self:
        secret = self.jwt_secret_key.get_secret_value()
        if self.environment in {"staging", "production"}:
            if len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be set to at least 32 characters in staging/production"
                )
        elif not secret:
            # Development/test convenience: an ephemeral key (tokens die on restart).
            self.jwt_secret_key = SecretStr(secrets.token_urlsafe(48))
            self._jwt_secret_generated = True
        return self

    @property
    def jwt_secret_generated(self) -> bool:
        return self._jwt_secret_generated

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def jira_configured(self) -> bool:
        return bool(
            self.jira_base_url and self.jira_email and self.jira_api_token.get_secret_value()
        )

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token.get_secret_value() and self.github_api_url)

    @property
    def keka_configured(self) -> bool:
        return bool(
            self.keka_base_url
            and self.keka_client_id
            and self.keka_client_secret.get_secret_value()
            and self.keka_api_key.get_secret_value()
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm1_model_name and self.llm_base_url)

    @property
    def gmail_configured(self) -> bool:
        has_env_credentials = bool(
            self.gmail_client_id
            and self.gmail_client_secret.get_secret_value()
            and self.gmail_refresh_token.get_secret_value()
        )
        return has_env_credentials or self.gmail_token_file.exists()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()


settings = get_settings()
