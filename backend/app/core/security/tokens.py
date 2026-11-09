"""Access tokens (short-lived JWTs) and refresh tokens (opaque, stored hashed)."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from pydantic import BaseModel, ValidationError

from backend.app.core.config.settings import Settings
from backend.app.core.exceptions.exceptions import AuthenticationError
from backend.app.core.security.permissions import Role
from backend.app.db.base import utcnow

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 (token type label, not a secret)


class AccessTokenClaims(BaseModel):
    sub: uuid.UUID
    role: Role
    typ: str
    jti: str
    iat: int
    exp: int


def create_access_token(
    user_id: uuid.UUID,
    role: Role,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = now or utcnow()
    expires_at = issued_at + timedelta(minutes=settings.access_token_ttl_minutes)
    claims = {
        "sub": str(user_id),
        "role": role.value,
        "typ": ACCESS_TOKEN_TYPE,
        "jti": uuid.uuid4().hex,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            # Pin the algorithm: never trust the token header's "alg" (e.g. "none").
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=10,
        )
        claims = AccessTokenClaims.model_validate(payload)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired", code="token_expired") from exc
    except (jwt.InvalidTokenError, ValidationError) as exc:
        raise AuthenticationError("Invalid access token") from exc

    if claims.typ != ACCESS_TOKEN_TYPE:
        raise AuthenticationError("Invalid access token")
    return claims


def generate_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, sha256_hash)``. Only the hash is persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
