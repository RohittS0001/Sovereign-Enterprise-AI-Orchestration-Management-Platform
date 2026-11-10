"""Compliance controls that depend only on configuration."""

from pydantic import SecretStr

from backend.app.core.config import Settings
from backend.app.domains.compliance.service import (
    ComplianceReport,
    _configuration,
    _data_protection,
)


def settings(**values) -> Settings:
    return Settings(
        **{"_env_file": None, "environment": "test", "jwt_secret_key": "k" * 40, **values}
    )


def test_demo_mode_in_production_fails_and_open_metrics_is_a_gap() -> None:
    prod = settings(
        environment="production", demo_mode=True, cors_origins=["https://app.example.com"]
    )
    result = _configuration(prod, {"jira": True, "github": False})

    assert result.status == "fail"
    assert any("Demo mode" in g for g in result.gaps) and any(
        "METRICS_TOKEN" in g for g in result.gaps
    )
    assert "Connected integrations: jira" in result.evidence[1]
    safe = settings(
        environment="production",
        metrics_token=SecretStr("m"),
        cors_origins=["https://a.example.com"],
    )
    assert _configuration(safe, {}).status == "pass"


def test_data_protection_flags_missing_allow_lists() -> None:
    open_ = _data_protection(settings(github_token=SecretStr("t")))
    assert open_.status == "warn" and len(open_.gaps) == 2
    closed = _data_protection(settings(gmail_allowed_recipient_domains=["example.com"]))
    assert closed.status == "pass"


def test_markdown_rendering() -> None:
    from datetime import UTC, datetime

    control = _data_protection(settings())
    report = ComplianceReport(generated_at=datetime(2026, 11, 9, tzinfo=UTC), summary={"warn": 1},
                              controls=[control])  # fmt: skip
    text = report.markdown()
    assert text.startswith("# Compliance evidence pack — 2026-11-09") and "**Gap:**" in text
