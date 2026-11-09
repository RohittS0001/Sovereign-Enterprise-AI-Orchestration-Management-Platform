import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config.settings import Settings
from backend.app.main import create_app


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked 'live' that call real external services.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") or os.getenv("RUN_LIVE_TESTS") == "1":
        return

    skip_live = pytest.mark.skip(reason="live test: pass --run-live to enable")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings isolated from the developer's .env and real credentials."""
    return Settings(
        _env_file=None,
        environment="test",
        log_level="WARNING",
        log_format="json",
        jira_base_url="",
        jira_email="",
        jira_api_token="",
        gmail_client_id="",
        gmail_client_secret="",
        gmail_refresh_token="",
        gmail_credentials_file=tmp_path / "credentials.json",
        gmail_token_file=tmp_path / "token.json",
        database_url="",
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client
