import json

import httpx
import pytest
from pydantic import SecretStr

from backend.app.core.errors import ConfigurationError, IntegrationError
from backend.app.integrations.jira.client import JiraClient

BASE_URL = "https://acme.atlassian.net"


def make_client(handler) -> JiraClient:
    return JiraClient(
        base_url=BASE_URL,
        email="bot@acme.com",
        api_token="token",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [
        ({"base_url": "", "email": "e", "api_token": "t"}, "JIRA_BASE_URL"),
        ({"base_url": BASE_URL, "email": "", "api_token": "t"}, "JIRA_EMAIL"),
        ({"base_url": BASE_URL, "email": "e", "api_token": ""}, "JIRA_API_TOKEN"),
    ],
)
def test_missing_configuration_raises(monkeypatch, kwargs, missing) -> None:
    from backend.app.integrations.jira import client as client_module

    monkeypatch.setattr(client_module.settings, "jira_base_url", "")
    monkeypatch.setattr(client_module.settings, "jira_email", "")
    monkeypatch.setattr(client_module.settings, "jira_api_token", SecretStr(""))

    with pytest.raises(ConfigurationError, match=missing):
        JiraClient(**kwargs)


@pytest.mark.asyncio
async def test_get_sends_auth_and_params() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={"accountId": "abc"})

    async with make_client(handler) as client:
        result = await client.get("/rest/api/3/myself", params={"expand": "groups"})

    request = seen["request"]
    assert result == {"accountId": "abc"}
    assert request.url.path == "/rest/api/3/myself"
    assert request.url.params["expand"] == "groups"
    assert request.headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_post_sends_json_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"fields": {"summary": "x"}}
        return httpx.Response(201, json={"key": "DEMO-1"})

    async with make_client(handler) as client:
        assert await client.post("/rest/api/3/issue", {"fields": {"summary": "x"}}) == {
            "key": "DEMO-1"
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [201, 204])
async def test_empty_responses_return_empty_dict(status_code) -> None:
    # Assign, update, transition and link endpoints reply with no body.
    async with make_client(lambda request: httpx.Response(status_code)) as client:
        assert await client.put("/rest/api/3/issue/DEMO-1/assignee", {"accountId": "a"}) == {}


@pytest.mark.asyncio
async def test_http_errors_become_integration_errors_with_jira_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"errorMessages": ["Bad JQL"], "errors": {"summary": "required"}},
        )

    async with make_client(handler) as client:
        with pytest.raises(IntegrationError) as exc_info:
            await client.get("/rest/api/3/search/jql")

    exc = exc_info.value
    assert exc.integration == "jira"
    assert exc.upstream_status == 400
    assert "Bad JQL" in exc.message
    assert "summary: required" in exc.message


@pytest.mark.asyncio
async def test_non_json_error_uses_reason_phrase() -> None:
    async with make_client(lambda request: httpx.Response(503, text="<html>")) as client:
        with pytest.raises(IntegrationError, match="Service Unavailable"):
            await client.get("/rest/api/3/myself")


@pytest.mark.asyncio
async def test_network_errors_become_integration_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with make_client(handler) as client:
        with pytest.raises(IntegrationError, match="Could not reach Jira"):
            await client.get("/rest/api/3/myself")


@pytest.mark.asyncio
async def test_timeouts_become_integration_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with make_client(handler) as client:
        with pytest.raises(IntegrationError, match="timed out"):
            await client.get("/rest/api/3/myself")
