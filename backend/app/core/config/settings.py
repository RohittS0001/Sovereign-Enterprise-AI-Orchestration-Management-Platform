"""Typed application settings loaded from environment variables and ``.env``."""

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "text"]


class Settings(BaseSettings):
    # Application
    app_name: str = "Sovereign Enterprise AI Orchestration & Management Platform"
    version: str = "0.1.0"
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

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

    # Local LLMs
    llm1_model_path: str = ""
    llm1_model_name: str = ""
    llm2_model_path: str = ""
    llm2_model_name: str = ""
    embedding_model_name: str = ""

    # Jira
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_timeout_seconds: float = Field(default=30.0, gt=0)

    # Gmail
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = SecretStr("")
    gmail_refresh_token: SecretStr = SecretStr("")
    gmail_redirect_uri: str = ""
    gmail_credentials_file: Path = PROJECT_ROOT / "credentials.json"
    gmail_token_file: Path = PROJECT_ROOT / "token.json"

    # Keka
    keka_base_url: str = ""
    keka_client_id: str = ""
    keka_client_secret: SecretStr = SecretStr("")

    # GitHub
    github_token: SecretStr = SecretStr("")

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
