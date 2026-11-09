import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from backend.app.api.deps import CurrentUserDep, SessionDep, require_permissions
from backend.app.core.security.permissions import Permission
from backend.app.schemas.auth import UserCreate, UserPage, UserRead, UserUpdate
from backend.app.services.user_service import UserService

router = APIRouter(
    prefix="/api/v1/users",
    tags=["Users"],
    dependencies=[Depends(require_permissions(Permission.USERS_MANAGE))],
)


@router.get("", response_model=UserPage)
async def list_users(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    users, total = await UserService(session).list(limit=limit, offset=offset)
    return UserPage(
        items=[UserRead.model_validate(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await UserService(session).create(body))


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await UserService(session).get(user_id))


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    session: SessionDep,
    acting_user: CurrentUserDep,
) -> UserRead:
    user = await UserService(session).update(user_id, body, acting_user=acting_user)
    return UserRead.model_validate(user)
