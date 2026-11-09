import uuid

import pytest

from backend.app.core.security.permissions import Role
from tests.conftest import TEST_PASSWORD


@pytest.fixture
async def admin_headers(make_user, auth_headers):
    return auth_headers(await make_user(email="admin@example.com", role=Role.ADMIN))


@pytest.mark.parametrize("role", [Role.MEMBER, Role.TEAM_LEAD, Role.PROJECT_MANAGER, Role.AUDITOR])
async def test_non_admins_cannot_manage_users(client, make_user, auth_headers, role) -> None:
    headers = auth_headers(await make_user(role=role))

    assert (await client.get("/api/v1/users", headers=headers)).status_code == 403
    assert (
        await client.post(
            "/api/v1/users",
            json={"email": "x@example.com", "full_name": "X", "password": TEST_PASSWORD},
            headers=headers,
        )
    ).status_code == 403


async def test_admin_creates_and_reads_user(client, admin_headers) -> None:
    created = await client.post(
        "/api/v1/users",
        json={
            "email": "New.Lead@Example.com",
            "full_name": "New Lead",
            "password": TEST_PASSWORD,
            "role": "team_lead",
        },
        headers=admin_headers,
    )

    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "new.lead@example.com"
    assert body["role"] == "team_lead"
    assert "hashed_password" not in body and "password" not in body

    fetched = await client.get(f"/api/v1/users/{body['id']}", headers=admin_headers)
    assert fetched.json()["email"] == "new.lead@example.com"

    login = await client.post(
        "/api/v1/auth/login", json={"email": "new.lead@example.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200


async def test_duplicate_email_is_a_conflict(client, admin_headers) -> None:
    payload = {"email": "dup@example.com", "full_name": "Dup", "password": TEST_PASSWORD}

    assert (
        await client.post("/api/v1/users", json=payload, headers=admin_headers)
    ).status_code == 201
    second = await client.post(
        "/api/v1/users", json={**payload, "email": "DUP@example.com"}, headers=admin_headers
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "full_name": "X", "password": TEST_PASSWORD},
        {"email": "a@example.com", "full_name": "X", "password": "Sh0rt!"},
        {"email": "a@example.com", "full_name": "X", "password": "onlylowercaseletters"},
        {"email": "a@example.com", "full_name": "X", "password": TEST_PASSWORD, "role": "god"},
        {"email": "a@example.com", "full_name": "", "password": TEST_PASSWORD},
    ],
)
async def test_create_user_validation(client, admin_headers, payload) -> None:
    response = await client.post("/api/v1/users", json=payload, headers=admin_headers)

    assert response.status_code == 422


async def test_list_users_paginates(client, admin_headers, make_user) -> None:
    for _ in range(3):
        await make_user()

    first = (await client.get("/api/v1/users?limit=2&offset=0", headers=admin_headers)).json()
    second = (await client.get("/api/v1/users?limit=2&offset=2", headers=admin_headers)).json()

    assert first["total"] == 4  # 3 users + the admin
    assert len(first["items"]) == 2
    assert len(second["items"]) == 2
    assert {u["id"] for u in first["items"]}.isdisjoint({u["id"] for u in second["items"]})


async def test_get_unknown_user_is_404(client, admin_headers) -> None:
    response = await client.get(f"/api/v1/users/{uuid.uuid4()}", headers=admin_headers)

    assert response.status_code == 404


async def test_admin_updates_role_and_name(client, admin_headers, make_user) -> None:
    user = await make_user(role=Role.MEMBER)

    response = await client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "project_manager", "full_name": "Promoted"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["role"] == "project_manager"
    assert response.json()["full_name"] == "Promoted"


async def test_deactivation_blocks_access_and_revokes_sessions(
    client, admin_headers, make_user, auth_headers
) -> None:
    user = await make_user(email="leaver@example.com")
    refresh_token = (
        await client.post(
            "/api/v1/auth/login", json={"email": "leaver@example.com", "password": TEST_PASSWORD}
        )
    ).json()["refresh_token"]

    response = await client.patch(
        f"/api/v1/users/{user.id}", json={"is_active": False}, headers=admin_headers
    )

    assert response.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=auth_headers(user))).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    ).status_code == 401


async def test_admin_cannot_lock_themselves_out(client, make_user, auth_headers) -> None:
    admin = await make_user(role=Role.ADMIN)
    headers = auth_headers(admin)

    demote = await client.patch(
        f"/api/v1/users/{admin.id}", json={"role": "member"}, headers=headers
    )
    deactivate = await client.patch(
        f"/api/v1/users/{admin.id}", json={"is_active": False}, headers=headers
    )
    rename = await client.patch(
        f"/api/v1/users/{admin.id}", json={"full_name": "Boss"}, headers=headers
    )

    assert demote.status_code == 409
    assert deactivate.status_code == 409
    assert rename.status_code == 200
