from backend.app.core.security.permissions import Role


async def test_agent_query_requires_authentication(client) -> None:
    response = await client.post("/api/v1/agent/query", json={"query": "status?"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_agent_query_accepts_json_body(client, make_user, auth_headers) -> None:
    user = await make_user(role=Role.MEMBER)

    response = await client.post(
        "/api/v1/agent/query",
        json={"query": "  Why is Phoenix late?  "},
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "received"
    assert body["query"] == "Why is Phoenix late?"


async def test_auditor_cannot_use_the_agent(client, make_user, auth_headers) -> None:
    auditor = await make_user(role=Role.AUDITOR)

    response = await client.post(
        "/api/v1/agent/query", json={"query": "hi"}, headers=auth_headers(auditor)
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "authorization_error"
    assert error["details"] == {"missing_permissions": ["agent:query"]}


async def test_agent_query_validation_errors(client, make_user, auth_headers) -> None:
    headers = auth_headers(await make_user())

    missing = await client.post("/api/v1/agent/query", json={}, headers=headers)
    blank = await client.post("/api/v1/agent/query", json={"query": "   "}, headers=headers)
    oversized = await client.post(
        "/api/v1/agent/query", json={"query": "x" * 4001}, headers=headers
    )

    assert missing.status_code == 422
    assert missing.json()["error"]["details"][0]["loc"] == ["body", "query"]
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_error"
    assert oversized.status_code == 422


async def test_agent_query_is_rate_limited_per_user(
    test_settings, database, make_user, auth_headers
) -> None:
    from backend.app.main import create_app
    from tests.conftest import serve

    settings = test_settings.model_copy(update={"agent_rate_limit_per_minute": 2})
    user = await make_user()
    other = await make_user()

    async with serve(create_app(settings, database)) as client:
        codes = [
            (
                await client.post(
                    "/api/v1/agent/query", json={"query": "q"}, headers=auth_headers(user)
                )
            ).status_code
            for _ in range(3)
        ]
        other_code = (
            await client.post(
                "/api/v1/agent/query", json={"query": "q"}, headers=auth_headers(other)
            )
        ).status_code

    assert codes == [200, 200, 429]
    assert other_code == 200
