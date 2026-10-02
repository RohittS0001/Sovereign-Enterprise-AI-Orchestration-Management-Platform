from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Sovereign Enterprise AI Orchestration & Management Platform"
    version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"

    host: str = "127.0.0.1"
    port: int = 8000

    # Jira
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()