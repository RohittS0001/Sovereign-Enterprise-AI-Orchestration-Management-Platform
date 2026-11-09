from fastapi import APIRouter
from fastapi.testclient import TestClient

from backend.app.core.exceptions.exceptions import IntegrationError, NotFoundError
from backend.app.main import create_app


def build_client(test_settings) -> TestClient:
    app = create_app(test_settings)
    router = APIRouter()

    @router.get("/boom/not-found")
    async def not_found() -> None:
        raise NotFoundError("Project DEMO not found", details={"project_key": "DEMO"})

    @router.get("/boom/integration")
    async def integration() -> None:
        raise IntegrationError("Jira is down", integration="jira", upstream_status=503)

    @router.get("/boom/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("secret internal detail")

    app.include_router(router)
    return TestClient(app)


def test_every_response_has_a_request_id(client) -> None:
    response = client.get("/api/v1/health")

    assert len(response.headers["X-Request-ID"]) == 32


def test_valid_incoming_request_id_is_propagated(client) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc_123"})

    assert response.headers["X-Request-ID"] == "trace-abc_123"


def test_unsafe_incoming_request_id_is_replaced(client) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "bad id\twith spaces"})

    assert response.headers["X-Request-ID"] != "bad id\twith spaces"
    assert len(response.headers["X-Request-ID"]) == 32


def test_app_exception_uses_standard_envelope(test_settings) -> None:
    with build_client(test_settings) as client:
        response = client.get("/boom/not-found", headers={"X-Request-ID": "req-1"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Project DEMO not found",
            "details": {"project_key": "DEMO"},
            "request_id": "req-1",
        }
    }


def test_integration_error_maps_to_502(test_settings) -> None:
    with build_client(test_settings) as client:
        response = client.get("/boom/integration")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "integration_error"


def test_unexpected_error_is_hidden_and_keeps_request_id(test_settings) -> None:
    with build_client(test_settings) as client:
        response = client.get("/boom/unexpected", headers={"X-Request-ID": "req-500"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-500"
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == "req-500"
    assert "secret internal detail" not in response.text


def test_unknown_route_uses_standard_envelope(client) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_docs_disabled_in_production(test_settings) -> None:
    production = test_settings.model_copy(update={"environment": "production"})

    with TestClient(create_app(production)) as client:
        assert client.get("/docs").status_code == 404
