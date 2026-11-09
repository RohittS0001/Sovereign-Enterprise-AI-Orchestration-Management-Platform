from fastapi import APIRouter, Response, status

from backend.app.api.deps import (
    ClientInfoDep,
    CurrentUserDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
)
from backend.app.core.security.permissions import Principal
from backend.app.schemas.auth import (
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    RefreshRequest,
    TokenPair,
    UserRead,
)
from backend.app.services.auth_service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client: ClientInfoDep,
) -> TokenPair:
    # Limit per client IP and per account, to slow both spraying and targeted guessing.
    for key in (f"login:ip:{client.ip_address}", f"login:email:{body.email}"):
        await limiter.hit(
            key,
            limit=settings.login_rate_limit_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
        )

    service = AuthService(session, settings)
    user = await service.authenticate(body.email, body.password)
    return await service.issue_tokens(user, client)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    session: SessionDep,
    settings: SettingsDep,
    client: ClientInfoDep,
) -> TokenPair:
    return await AuthService(session, settings).refresh(body.refresh_token, client)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: SessionDep, settings: SettingsDep) -> Response:
    await AuthService(session, settings).logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=CurrentUser)
async def me(user: CurrentUserDep) -> CurrentUser:
    principal = Principal.human(user.id, user.email, user.role)
    return CurrentUser(
        **UserRead.model_validate(user).model_dump(),
        permissions=sorted(principal.permissions),
        agent_permissions=sorted(principal.as_agent().permissions),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    await AuthService(session, settings).change_password(
        user, body.current_password, body.new_password
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
