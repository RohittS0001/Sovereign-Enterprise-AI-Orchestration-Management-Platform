import uuid
from datetime import timedelta

import jwt
import pytest

from backend.app.core.config.settings import Settings
from backend.app.core.exceptions.exceptions import AuthenticationError, RateLimitError
from backend.app.core.security.passwords import hash_password, needs_rehash, verify_password
from backend.app.core.security.permissions import (
    AGENT_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    Principal,
    Role,
)
from backend.app.core.security.rate_limit import RateLimiter
from backend.app.core.security.tokens import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from backend.app.db.base import utcnow
from tests.conftest import TEST_JWT_SECRET

# ------------------------------------------------------------------ passwords


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("Str0ng!Passw0rd")

    assert hashed.startswith("$argon2id$")
    assert hashed != "Str0ng!Passw0rd"
    assert verify_password("Str0ng!Passw0rd", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert needs_rehash(hashed) is False


@pytest.mark.parametrize("stored", [None, "", "not-a-real-hash"])
def test_verify_password_handles_missing_or_corrupt_hashes(stored) -> None:
    assert verify_password("anything", stored) is False


# ------------------------------------------------------------------ tokens


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, environment="test", jwt_secret_key=TEST_JWT_SECRET)


def test_access_token_roundtrip(settings) -> None:
    user_id = uuid.uuid4()

    token, expires_at = create_access_token(user_id, Role.TEAM_LEAD, settings)
    claims = decode_access_token(token, settings)

    assert claims.sub == user_id
    assert claims.role == Role.TEAM_LEAD
    assert claims.typ == "access"
    assert expires_at > utcnow()


def test_expired_token_is_rejected(settings) -> None:
    token, _ = create_access_token(
        uuid.uuid4(), Role.MEMBER, settings, now=utcnow() - timedelta(hours=1)
    )

    with pytest.raises(AuthenticationError) as exc_info:
        decode_access_token(token, settings)
    assert exc_info.value.code == "token_expired"


def test_token_signed_with_other_key_is_rejected(settings) -> None:
    other = settings.model_copy(update={"jwt_secret_key": type(settings.jwt_secret_key)("x" * 40)})
    token, _ = create_access_token(uuid.uuid4(), Role.MEMBER, other)

    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings)


@pytest.mark.parametrize("field", ["jwt_audience", "jwt_issuer"])
def test_token_for_other_audience_or_issuer_is_rejected(settings, field) -> None:
    token, _ = create_access_token(
        uuid.uuid4(), Role.MEMBER, settings.model_copy(update={field: "someone-else"})
    )

    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings)


def test_unsigned_alg_none_token_is_rejected(settings) -> None:
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "admin",
        "typ": "access",
        "jti": "x",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(utcnow().timestamp()),
        "exp": int((utcnow() + timedelta(minutes=5)).timestamp()),
    }
    forged = jwt.encode(payload, key=None, algorithm="none")

    with pytest.raises(AuthenticationError):
        decode_access_token(forged, settings)


def test_token_with_wrong_type_is_rejected(settings) -> None:
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "member",
        "typ": "refresh",
        "jti": "x",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(utcnow().timestamp()),
        "exp": int((utcnow() + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings)


def test_refresh_tokens_are_random_and_hashed() -> None:
    raw1, hash1 = generate_refresh_token()
    raw2, _ = generate_refresh_token()

    assert raw1 != raw2
    assert len(raw1) >= 64
    assert hash1 == hash_refresh_token(raw1)
    assert raw1 not in hash1


# ------------------------------------------------------------------ settings


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_strong_jwt_secret_required_outside_development(environment) -> None:
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, environment=environment, jwt_secret_key="short")

    ok = Settings(_env_file=None, environment=environment, jwt_secret_key="k" * 32)
    assert ok.jwt_secret_generated is False


def test_development_generates_ephemeral_secret() -> None:
    settings = Settings(_env_file=None, environment="development", jwt_secret_key="")

    assert settings.jwt_secret_generated is True
    assert len(settings.jwt_secret_key.get_secret_value()) >= 32


# ------------------------------------------------------------------ permissions


def test_admin_has_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)


def test_roles_form_a_hierarchy() -> None:
    member = ROLE_PERMISSIONS[Role.MEMBER]
    lead = ROLE_PERMISSIONS[Role.TEAM_LEAD]
    pm = ROLE_PERMISSIONS[Role.PROJECT_MANAGER]

    assert member < lead < pm < ROLE_PERMISSIONS[Role.ADMIN]


def test_auditor_is_read_only() -> None:
    auditor = ROLE_PERMISSIONS[Role.AUDITOR]

    assert Permission.AUDIT_READ in auditor
    assert not auditor & {
        Permission.AGENT_QUERY,
        Permission.PROJECTS_WRITE,
        Permission.EMAIL_SEND,
        Permission.USERS_MANAGE,
        Permission.APPROVALS_DECIDE,
    }


@pytest.mark.parametrize("role", list(Role))
def test_agent_never_exceeds_user_or_its_scope(role) -> None:
    human = Principal.human(uuid.uuid4(), "u@example.com", role)
    agent = human.as_agent()

    assert agent.actor_type == "agent"
    assert agent.permissions <= human.permissions
    assert agent.permissions <= AGENT_SCOPES
    assert Permission.APPROVALS_DECIDE not in agent.permissions
    assert Permission.USERS_MANAGE not in agent.permissions
    assert Permission.AUDIT_READ not in agent.permissions


def test_agent_writes_require_approval_but_human_writes_do_not() -> None:
    lead = Principal.human(uuid.uuid4(), "lead@example.com", Role.TEAM_LEAD)

    assert lead.requires_approval(Permission.PROJECTS_WRITE) is False
    assert lead.as_agent().requires_approval(Permission.PROJECTS_WRITE) is True
    assert lead.as_agent().requires_approval(Permission.EMAIL_SEND) is True
    assert lead.as_agent().requires_approval(Permission.PROJECTS_READ) is False


def test_member_agent_cannot_write_even_with_approval() -> None:
    member_agent = Principal.human(uuid.uuid4(), "m@example.com", Role.MEMBER).as_agent()

    assert member_agent.has(Permission.PROJECTS_READ)
    assert not member_agent.has(Permission.PROJECTS_WRITE)


# ------------------------------------------------------------------ rate limiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_rate_limiter_blocks_then_recovers() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)

    for _ in range(3):
        await limiter.hit("k", limit=3, window_seconds=60)

    with pytest.raises(RateLimitError) as exc_info:
        await limiter.hit("k", limit=3, window_seconds=60)
    assert exc_info.value.headers["Retry-After"] == "60"

    clock.now += 60
    await limiter.hit("k", limit=3, window_seconds=60)


async def test_rate_limiter_keys_are_independent_and_resettable() -> None:
    limiter = RateLimiter(clock=FakeClock())

    await limiter.hit("a", limit=1, window_seconds=60)
    await limiter.hit("b", limit=1, window_seconds=60)
    with pytest.raises(RateLimitError):
        await limiter.hit("a", limit=1, window_seconds=60)

    await limiter.reset("a")
    await limiter.hit("a", limit=1, window_seconds=60)
