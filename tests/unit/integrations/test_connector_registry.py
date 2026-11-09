"""The connector registry stays complete: every integration is configurable from .env."""

import re
from pathlib import Path

from pydantic import SecretStr

from backend.app.core.config import Settings
from backend.app.integrations.connectors.registry import ALL_SPECS, CONNECTOR_SPECS, spec
from scripts.connections import markdown

ROOT = Path(__file__).resolve().parents[3]


def settings(**values) -> Settings:
    return Settings(_env_file=None, environment="test", **values)


def test_spec_names_are_unique_and_fields_exist() -> None:
    names = [s.name for s in ALL_SPECS]
    assert len(names) == len(set(names))
    fields = Settings.model_fields
    for item in ALL_SPECS:
        assert item.required, item.name
        for field_name in (*item.required, *item.optional):
            assert field_name in fields, f"{item.name}: Settings has no field {field_name}"


def test_env_example_lists_every_connector_variable() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    listed = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example, re.MULTILINE))
    missing = {v for s in ALL_SPECS for v in s.env_vars} - listed
    assert not missing, f"add to .env.example: {sorted(missing)}"


def test_connection_guide_is_up_to_date() -> None:
    doc = (ROOT / "docs" / "integrations.md").read_text(encoding="utf-8")
    assert doc == markdown(), "run: python -m scripts.connections --markdown"


def test_live_connectors_build_without_network() -> None:
    # Every connector with a live implementation builds lazily from settings alone.
    for item in CONNECTOR_SPECS:
        assert item.build is not None and item.demo is not None, item.name


def test_missing_reports_only_unset_required_variables() -> None:
    jira = spec("jira")
    partial = settings(jira_base_url="https://acme.atlassian.net")

    assert jira.missing(partial) == ["JIRA_EMAIL", "JIRA_API_TOKEN"]
    full = settings(
        jira_base_url="https://acme.atlassian.net",
        jira_email="a@example.com",
        jira_api_token=SecretStr("t"),
    )
    assert jira.configured(full) and jira.missing(full) == []


def test_gmail_accepts_a_token_file_instead_of_env_credentials(tmp_path) -> None:
    token = tmp_path / "token.json"
    gmail = spec("gmail")

    assert not gmail.configured(settings(gmail_token_file=token))
    token.write_text("{}")
    assert gmail.configured(settings(gmail_token_file=token))
