from datetime import datetime

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.security.permissions import Role
from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


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
