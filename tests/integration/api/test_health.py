from pydantic import SecretStr

from backend.app.db.session import Database
from backend.app.main import create_app
from tests.conftest import serve


async def test_health_returns_liveness(client) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "backend"
    assert body["environment"] == "test"
    assert body["version"]


async def test_readiness_reports_each_dependency(client) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    deps = {d["name"]: d for d in body["dependencies"]}
    assert deps["database"] == {"name": "database", "configured": True, "healthy": True}
    assert deps["jira"]["configured"] is False
    assert deps["gmail"]["configured"] is False


async def test_readiness_without_database(test_settings) -> None:
    app = create_app(test_settings.model_copy(update={"database_url": ""}))

    async with serve(app) as client:
        deps = {
            d["name"]: d for d in (await client.get("/api/v1/health/ready")).json()["dependencies"]
        }

    assert deps["database"] == {"name": "database", "configured": False, "healthy": False}


async def test_readiness_is_ready_when_everything_is_configured(test_settings, database) -> None:
    configured = test_settings.model_copy(
        update={
            "jira_base_url": "https://acme.atlassian.net",
            "jira_email": "bot@acme.com",
            "jira_api_token": SecretStr("token"),
        }
    )
    configured.gmail_token_file.write_text("{}", encoding="utf-8")

    async with serve(create_app(configured, database)) as client:
        assert (await client.get("/api/v1/health/ready")).json()["status"] == "ready"


async def test_readiness_degraded_when_database_unreachable(test_settings, tmp_path) -> None:
    # A SQLite path inside a missing directory cannot be opened.
    broken = Database(f"sqlite+aiosqlite:///{tmp_path / 'missing' / 'x.db'}")

    async with serve(create_app(test_settings, broken)) as client:
        body = (await client.get("/api/v1/health/ready")).json()

    deps = {d["name"]: d for d in body["dependencies"]}
    assert body["status"] == "degraded"
    assert deps["database"]["healthy"] is False
