from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.main import create_app


def test_health_returns_liveness(client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "backend"
    assert body["environment"] == "test"
    assert body["version"]


def test_readiness_reports_unconfigured_dependencies(client) -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert {d["name"]: d["configured"] for d in body["dependencies"]} == {
        "jira": False,
        "gmail": False,
        "database": False,
    }


def test_readiness_is_ready_when_everything_is_configured(test_settings) -> None:
    configured = test_settings.model_copy(
        update={
            "jira_base_url": "https://acme.atlassian.net",
            "jira_email": "bot@acme.com",
            "jira_api_token": SecretStr("token"),
            "database_url": "postgresql://localhost/db",
        }
    )
    configured.gmail_token_file.write_text("{}", encoding="utf-8")

    with TestClient(create_app(configured)) as client:
        assert client.get("/api/v1/health/ready").json()["status"] == "ready"
