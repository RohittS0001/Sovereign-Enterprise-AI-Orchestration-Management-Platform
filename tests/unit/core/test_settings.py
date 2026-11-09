from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_defaults() -> None:
    settings = make(jira_base_url="", jira_email="", jira_api_token="")

    assert settings.environment in {"development", "test", "staging", "production"}
    assert settings.port == 8000
    assert settings.jira_configured is False


def test_environment_and_levels_are_normalized() -> None:
    settings = make(
        environment=" Production ",
        log_level="debug",
        log_format="TEXT",
        jwt_secret_key="p" * 32,
    )

    assert settings.environment == "production"
    assert settings.is_production is True
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "text"


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "qa"},
        {"log_level": "verbose"},
        {"port": 0},
        {"port": 70000},
        {"jira_base_url": "ftp://example.com"},
    ],
)
def test_invalid_values_are_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        make(**overrides)


def test_jira_url_is_trimmed_and_configured_flag() -> None:
    settings = make(
        jira_base_url="https://acme.atlassian.net/ ",
        jira_email="bot@acme.com",
        jira_api_token="secret",
    )

    assert settings.jira_base_url == "https://acme.atlassian.net"
    assert settings.jira_configured is True


def test_secrets_are_not_exposed_in_repr() -> None:
    settings = make(jira_api_token="super-secret-token")

    assert "super-secret-token" not in repr(settings)
    assert settings.jira_api_token.get_secret_value() == "super-secret-token"


def test_gmail_configured_from_env_or_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    base = {
        "gmail_client_id": "",
        "gmail_client_secret": "",
        "gmail_refresh_token": "",
        "gmail_token_file": token_file,
    }

    assert make(**base).gmail_configured is False

    token_file.write_text("{}", encoding="utf-8")
    assert make(**base).gmail_configured is True

    token_file.unlink()
    env_based = {
        **base,
        "gmail_client_id": "id",
        "gmail_client_secret": "s",
        "gmail_refresh_token": "r",
    }
    assert make(**env_based).gmail_configured is True
