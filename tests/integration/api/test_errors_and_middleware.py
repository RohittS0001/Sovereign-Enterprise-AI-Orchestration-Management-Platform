from fastapi import APIRouter, FastAPI

from backend.app.core.errors import IntegrationError, NotFoundError
from backend.app.main import create_app
from tests.conftest import serve


def build_app(test_settings) -> FastAPI:
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
    return app


async def test_every_response_has_a_request_id(client) -> None:
    response = await client.get("/api/v1/health")

    assert len(response.headers["X-Request-ID"]) == 32


async def test_valid_incoming_request_id_is_propagated(client) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc_123"})

    assert response.headers["X-Request-ID"] == "trace-abc_123"


async def test_unsafe_incoming_request_id_is_replaced(client) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "bad id\twith spaces"})

    assert response.headers["X-Request-ID"] != "bad id\twith spaces"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_app_exception_uses_standard_envelope(test_settings) -> None:
    async with serve(build_app(test_settings)) as client:
        response = await client.get("/boom/not-found", headers={"X-Request-ID": "req-1"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Project DEMO not found",
            "details": {"project_key": "DEMO"},
            "request_id": "req-1",
        }
    }


async def test_integration_error_maps_to_502(test_settings) -> None:
    async with serve(build_app(test_settings)) as client:
        response = await client.get("/boom/integration")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "integration_error"


async def test_unexpected_error_is_hidden_and_keeps_request_id(test_settings) -> None:
    async with serve(build_app(test_settings)) as client:
        response = await client.get("/boom/unexpected", headers={"X-Request-ID": "req-500"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-500"
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == "req-500"
    assert "secret internal detail" not in response.text


async def test_unknown_route_uses_standard_envelope(client) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_docs_disabled_in_production(test_settings) -> None:
    production = test_settings.model_copy(update={"environment": "production", "database_url": ""})

    async with serve(create_app(production)) as client:
        assert (await client.get("/docs")).status_code == 404


async def test_security_headers_are_set(client) -> None:
    response = await client.get("/api/v1/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Strict-Transport-Security" not in response.headers


async def test_hsts_enabled_in_production(test_settings) -> None:
    production = test_settings.model_copy(update={"environment": "production", "database_url": ""})

    async with serve(create_app(production)) as client:
        response = await client.get("/api/v1/health")

    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


async def test_rate_limit_error_sets_retry_after(test_settings) -> None:
    from backend.app.core.errors import RateLimitError

    app = create_app(test_settings)
    router = APIRouter()

    @router.get("/boom/limited")
    async def limited() -> None:
        raise RateLimitError(42)

    app.include_router(router)

    async with serve(app) as client:
        response = await client.get("/boom/limited")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.json()["error"]["details"] == {"retry_after_seconds": 42}
