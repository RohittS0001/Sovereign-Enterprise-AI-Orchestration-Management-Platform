"""Login, token issuance, refresh-token rotation and logout."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit import actions
from backend.app.audit.service import AuditService
from backend.app.core.config.settings import Settings
from backend.app.core.exceptions.exceptions import AuthenticationError
from backend.app.core.logging.logger import get_logger
from backend.app.core.security.passwords import hash_password, needs_rehash, verify_password
from backend.app.core.security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from backend.app.db.base import as_utc, utcnow
from backend.app.db.models import RefreshToken, User
from backend.app.schemas.auth import TokenPair

logger = get_logger(__name__)

_INVALID_CREDENTIALS = "Invalid email or password"


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every active refresh token of a user (caller commits)."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


@dataclass(frozen=True)
class ClientInfo:
    user_agent: str | None = None
    ip_address: str | None = None


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.session.scalar(select(User).where(User.email == email.lower()))

        # Same error (and similar timing) for unknown user, wrong password and
        # disabled account, so the endpoint cannot be used to discover accounts.
        if user is None or not verify_password(password, user.hashed_password):
            logger.warning("Failed login", extra={"reason": "bad_credentials"})
            raise AuthenticationError(_INVALID_CREDENTIALS)
        if not user.is_active:
            logger.warning("Failed login", extra={"reason": "inactive", "user_id": str(user.id)})
            raise AuthenticationError(_INVALID_CREDENTIALS)

        if user.hashed_password and needs_rehash(user.hashed_password):
            user.hashed_password = hash_password(password)

        user.last_login_at = utcnow()
        return user

    async def issue_tokens(self, user: User, client: ClientInfo) -> TokenPair:
        access_token, _ = create_access_token(user.id, user.role, self.settings)
        refresh_record, raw_refresh = self._new_refresh_token(user.id, client)
        self.session.add(refresh_record)
        await self.session.commit()

        logger.info("Tokens issued", extra={"user_id": str(user.id)})
        return self._token_pair(access_token, raw_refresh)

    async def refresh(self, raw_token: str, client: ClientInfo) -> TokenPair:
        record = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        )
        if record is None:
            raise AuthenticationError("Invalid refresh token")

        if record.revoked_at is not None:
            # A rotated token was replayed: assume it was stolen and end every session.
            await revoke_user_sessions(self.session, record.user_id)
            await AuditService(self.session).record(
                actions.AUTH_REFRESH_REUSE,
                status=actions.DENIED,
                actor_type="human",
                actor_user_id=record.user_id,
                details={"ip": client.ip_address, "action": "all_sessions_revoked"},
            )
            await self.session.commit()
            logger.warning("Refresh token reuse detected", extra={"user_id": str(record.user_id)})
            raise AuthenticationError("Refresh token has been revoked")

        if as_utc(record.expires_at) <= utcnow():
            raise AuthenticationError("Refresh token has expired", code="token_expired")

        user = await self.session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token")

        new_record, raw_refresh = self._new_refresh_token(user.id, client)
        record.revoked_at = utcnow()
        record.replaced_by_id = new_record.id
        self.session.add(new_record)
        await self.session.commit()

        access_token, _ = create_access_token(user.id, user.role, self.settings)
        return self._token_pair(access_token, raw_refresh)

    async def logout(self, raw_token: str) -> None:
        """Revoke one session. Unknown tokens are ignored so logout is idempotent."""
        record = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        )
        if record is not None and record.revoked_at is None:
            record.revoked_at = utcnow()
            await self.session.commit()

    async def change_password(self, user: User, current: str, new: str) -> None:
        if not verify_password(current, user.hashed_password):
            raise AuthenticationError("Current password is incorrect")
        user.hashed_password = hash_password(new)
        await revoke_user_sessions(self.session, user.id)
        await self.session.commit()
        logger.info("Password changed; sessions revoked", extra={"user_id": str(user.id)})

    def _new_refresh_token(
        self, user_id: uuid.UUID, client: ClientInfo
    ) -> tuple[RefreshToken, str]:
        raw, token_hash = generate_refresh_token()
        record = RefreshToken(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=utcnow() + timedelta(days=self.settings.refresh_token_ttl_days),
            user_agent=(client.user_agent or "")[:500] or None,
            ip_address=client.ip_address,
        )
        return record, raw

    def _token_pair(self, access_token: str, refresh_token: str) -> TokenPair:
        return TokenPair(
            access_token=access_token,
            expires_in=self.settings.access_token_ttl_minutes * 60,
            refresh_token=refresh_token,
            refresh_expires_in=self.settings.refresh_token_ttl_days * 24 * 3600,
        )
