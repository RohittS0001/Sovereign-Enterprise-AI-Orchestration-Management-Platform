import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from backend.app.core.security.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from backend.app.core.security.permissions import Permission, Role


def _check_password_strength(value: str) -> str:
    classes = [
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ]
    if sum(classes) < 3:
        raise ValueError(
            "Password must contain at least three of: lowercase, uppercase, digit, symbol"
        )
    return value


NewPassword = Annotated[
    str,
    Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH),
    AfterValidator(_check_password_strength),
]

NormalizedEmail = Annotated[EmailStr, AfterValidator(lambda value: value.lower())]


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: NewPassword


class TokenPair(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (OAuth token type, not a secret)
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_token: str
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    auth_provider: str
    last_login_at: datetime | None
    created_at: datetime


class CurrentUser(UserRead):
    permissions: list[Permission]
    agent_permissions: list[Permission]


class UserCreate(BaseModel):
    email: NormalizedEmail
    full_name: str = Field(min_length=1, max_length=200)
    password: NewPassword
    role: Role = Role.MEMBER


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None


class UserPage(BaseModel):
    items: list[UserRead]
    total: int
    limit: int
    offset: int
