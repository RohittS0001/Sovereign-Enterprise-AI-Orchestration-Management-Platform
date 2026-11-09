import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.security.permissions import Role
from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Stored lower-cased so uniqueness is case-insensitive on every database.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    # Null for users who sign in through an external identity provider (SSO).
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            native_enum=False,
            length=32,
            values_callable=lambda roles: [r.value for r in roles],
            validate_strings=True,
        ),
        default=Role.MEMBER,
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    auth_provider: Mapped[str] = mapped_column(String(50), default="local")
    last_login_at: Mapped[datetime | None]

    def __repr__(self) -> str:
        return f"User(id={self.id}, email={self.email!r}, role={self.role})"


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """A long-lived session credential. Only a SHA-256 hash of the token is stored.

    Tokens rotate on every use. Presenting an already-rotated token signals theft, and
    the whole family (every session of that user) is revoked.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None]
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class ApiToken(UUIDPrimaryKeyMixin, Base):
    """A personal access token for machine clients (MCP). Only a SHA-256 hash is stored.

    Tokens act as their owner with the agent's boundary (``principal.as_agent()``), are
    accepted only on the MCP endpoint, expire, and can be revoked at any time.
    """

    __tablename__ = "api_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))  # shown in lists to tell tokens apart
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
