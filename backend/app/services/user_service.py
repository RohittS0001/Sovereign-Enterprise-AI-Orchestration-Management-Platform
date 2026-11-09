"""User administration."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions.exceptions import ConflictError, NotFoundError
from backend.app.core.logging.logger import get_logger
from backend.app.core.security.passwords import hash_password
from backend.app.core.security.permissions import Role
from backend.app.db.models import User
from backend.app.schemas.auth import UserCreate, UserUpdate
from backend.app.services.auth_service import revoke_user_sessions

logger = get_logger(__name__)


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: UserCreate) -> User:
        user = User(
            email=data.email.lower(),
            full_name=data.full_name,
            hashed_password=hash_password(data.password),
            role=data.role,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A user with this email already exists") from exc

        logger.info("User created", extra={"user_id": str(user.id), "role": user.role.value})
        return user

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def list(self, *, limit: int, offset: int) -> tuple[list[User], int]:
        total = await self.session.scalar(select(func.count()).select_from(User)) or 0
        result = await self.session.scalars(
            select(User).order_by(User.created_at, User.id).limit(limit).offset(offset)
        )
        return list(result), total

    async def update(self, user_id: uuid.UUID, data: UserUpdate, *, acting_user: User) -> User:
        user = await self.get(user_id)
        changes = data.model_dump(exclude_unset=True, exclude_none=True)

        if user.id == acting_user.id and (
            changes.get("is_active") is False
            or ("role" in changes and changes["role"] != Role.ADMIN)
        ):
            # Prevents an admin from locking everyone (including themselves) out.
            raise ConflictError("You cannot deactivate or demote your own account")

        for field, value in changes.items():
            setattr(user, field, value)

        if changes.get("is_active") is False:
            await revoke_user_sessions(self.session, user.id)

        await self.session.commit()
        logger.info(
            "User updated",
            extra={
                "user_id": str(user.id),
                "fields": sorted(changes),
                "by": str(acting_user.id),
            },
        )
        return user
