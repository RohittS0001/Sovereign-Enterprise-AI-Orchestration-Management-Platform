"""Personal access tokens for MCP clients (Claude Desktop, IDEs, internal bots)."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AuthenticationError, ConflictError, NotFoundError
from backend.app.db.base import as_utc, utcnow
from backend.app.domains.identity.models import ApiToken, User

TOKEN_PREFIX = "sov_pat_"  # noqa: S105 (a prefix, not a secret)
MAX_TOKENS_PER_USER = 20


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, user: User, name: str, expires_in_days: int | None
    ) -> tuple[str, ApiToken]:
        """Returns the plain token (shown once) and its stored record."""
        active = await self.list(user.id)
        if len(active) >= MAX_TOKENS_PER_USER:
            raise ConflictError(f"At most {MAX_TOKENS_PER_USER} active tokens per user")
        plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = ApiToken(
            user_id=user.id,
            name=name.strip(),
            prefix=plain[: len(TOKEN_PREFIX) + 6],
            token_hash=hash_token(plain),
            expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
        )
        self.session.add(record)
        await self.session.flush()
        return plain, record

    async def list(self, user_id: uuid.UUID) -> list[ApiToken]:
        rows = await self.session.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc())
        )
        now = utcnow()
        return [t for t in rows if t.expires_at is None or as_utc(t.expires_at) > now]

    async def revoke(self, user_id: uuid.UUID, token_id: uuid.UUID) -> ApiToken:
        record = await self.session.get(ApiToken, token_id)
        if record is None or record.user_id != user_id or record.revoked_at is not None:
            raise NotFoundError("Token not found")
        record.revoked_at = utcnow()
        return record

    async def authenticate(self, token: str) -> User:
        """The active owner of a valid, unexpired, unrevoked token (or AuthenticationError)."""
        invalid = AuthenticationError("Invalid or expired access token")
        if not token.startswith(TOKEN_PREFIX):
            raise invalid
        record = await self.session.scalar(
            select(ApiToken).where(ApiToken.token_hash == hash_token(token))
        )
        now = utcnow()
        if (
            record is None
            or record.revoked_at is not None
            or (record.expires_at is not None and as_utc(record.expires_at) <= now)
        ):
            raise invalid
        user = await self.session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise invalid
        record.last_used_at = now
        await self.session.commit()
        return user
