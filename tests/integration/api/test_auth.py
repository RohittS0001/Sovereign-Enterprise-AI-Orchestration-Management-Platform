from datetime import timedelta

from sqlalchemy import select, update

from backend.app.core.security.permissions import Role
from backend.app.db.base import utcnow
from backend.app.db.models import RefreshToken, User
from backend.app.main import create_app
from tests.conftest import TEST_PASSWORD, serve

NEW_PASSWORD = "An0ther!Passw0rd"  # noqa: S105 (test-only credential)


async def login(client, email: str, password: str = TEST_PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_login_returns_token_pair(client, make_user) -> None:
    user = await make_user(email="pm@example.com", role=Role.PROJECT_MANAGER)

    response = await login(client, "PM@Example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"] and body["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)


async def test_login_failures_are_indistinguishable(client, make_user) -> None:
    await make_user(email="real@example.com")
    await make_user(email="disabled@example.com", is_active=False)

    wrong_password = await login(client, "real@example.com", "Wr0ng!Password")
    unknown_user = await login(client, "nobody@example.com")
    disabled = await login(client, "disabled@example.com")

    for response in (wrong_password, unknown_user, disabled):
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Invalid email or password"


async def test_login_records_last_login(client, make_user, database) -> None:
    user = await make_user(email="seen@example.com")

    await login(client, "seen@example.com")

    async with database.session() as session:
        assert (await session.get(User, user.id)).last_login_at is not None


async def test_login_is_rate_limited(test_settings, database, make_user) -> None:
    settings = test_settings.model_copy(update={"login_rate_limit_attempts": 3})
    await make_user(email="target@example.com")

    async with serve(create_app(settings, database)) as client:
        codes = [
            (await login(client, "target@example.com", "Wr0ng!Password")).status_code
            for _ in range(4)
        ]
        # The limit also blocks the correct password once exceeded.
        blocked = await login(client, "target@example.com")

    assert codes == [401, 401, 401, 429]
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


async def test_me_lists_human_and_agent_permissions(client, make_user, auth_headers) -> None:
    admin = await make_user(role=Role.ADMIN)

    body = (await client.get("/api/v1/auth/me", headers=auth_headers(admin))).json()

    assert "users:manage" in body["permissions"]
    assert "approvals:decide" in body["permissions"]
    # The agent never inherits admin-only or approval powers.
    assert "users:manage" not in body["agent_permissions"]
    assert "approvals:decide" not in body["agent_permissions"]
    assert "audit:read" not in body["agent_permissions"]


async def test_missing_and_invalid_tokens_are_rejected(client) -> None:
    missing = await client.get("/api/v1/auth/me")
    garbage = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    basic = await client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert garbage.status_code == 401
    assert basic.status_code == 401


async def test_token_for_deactivated_user_is_rejected(
    client, make_user, auth_headers, database
) -> None:
    user = await make_user()
    headers = auth_headers(user)

    async with database.session() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()

    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_role_change_applies_immediately(client, make_user, auth_headers, database) -> None:
    user = await make_user(role=Role.ADMIN)
    headers = auth_headers(user)  # token still says "admin"

    async with database.session() as session:
        await session.execute(update(User).where(User.id == user.id).values(role=Role.MEMBER))
        await session.commit()

    assert (await client.get("/api/v1/users", headers=headers)).status_code == 403


async def test_refresh_rotates_tokens(client, make_user) -> None:
    await make_user(email="rot@example.com")
    first = (await login(client, "rot@example.com")).json()

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )

    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]

    # The new refresh token works...
    again = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert again.status_code == 200


async def test_refresh_token_reuse_revokes_every_session(client, make_user, database) -> None:
    user = await make_user(email="theft@example.com")
    stolen = (await login(client, "theft@example.com")).json()["refresh_token"]
    other_session = (await login(client, "theft@example.com")).json()["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert rotated.status_code == 200

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 401

    # Every session for the user, including the legitimate rotated one, is now dead.
    for token in (rotated.json()["refresh_token"], other_session):
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        ).status_code == 401

    async with database.session() as session:
        active = await session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
            )
        )
        assert list(active) == []


async def test_expired_refresh_token_is_rejected(client, make_user, database) -> None:
    await make_user(email="old@example.com")
    token = (await login(client, "old@example.com")).json()["refresh_token"]

    async with database.session() as session:
        await session.execute(
            update(RefreshToken).values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def test_unknown_refresh_token_is_rejected(client) -> None:
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": "nope"})

    assert response.status_code == 401


async def test_logout_revokes_the_session_and_is_idempotent(client, make_user) -> None:
    await make_user(email="bye@example.com")
    token = (await login(client, "bye@example.com")).json()["refresh_token"]

    first = await client.post("/api/v1/auth/logout", json={"refresh_token": token})
    second = await client.post("/api/v1/auth/logout", json={"refresh_token": token})

    assert first.status_code == 204
    assert second.status_code == 204
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    ).status_code == 401


async def test_change_password_revokes_sessions(client, make_user, auth_headers) -> None:
    user = await make_user(email="cp@example.com")
    refresh_token = (await login(client, "cp@example.com")).json()["refresh_token"]

    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        headers=auth_headers(user),
    )

    assert response.status_code == 204
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    ).status_code == 401
    assert (await login(client, "cp@example.com")).status_code == 401
    assert (await login(client, "cp@example.com", NEW_PASSWORD)).status_code == 200


async def test_change_password_checks_current_and_strength(client, make_user, auth_headers) -> None:
    headers = auth_headers(await make_user())

    wrong_current = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "Wr0ng!Password", "new_password": NEW_PASSWORD},
        headers=headers,
    )
    weak = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "alllowercase1"},
        headers=headers,
    )

    assert wrong_current.status_code == 401
    assert weak.status_code == 422


async def test_auth_endpoints_report_missing_database(test_settings) -> None:
    app = create_app(test_settings.model_copy(update={"database_url": ""}))

    async with serve(app) as client:
        response = await login(client, "x@example.com")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
