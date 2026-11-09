"""Shared FastAPI dependencies: settings, database sessions, auth and rate limits."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ServiceUnavailableError,
)
from backend.app.core.security.permissions import Permission, Principal
from backend.app.core.security.rate_limit import RateLimiter
from backend.app.core.security.tokens import decode_access_token
from backend.app.db.models import User
from backend.app.db.session import Database
from backend.app.domains.identity.auth_service import ClientInfo
from backend.app.llm.base import AgentModels
from backend.app.tools.registry import ToolRegistry

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_database(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise ServiceUnavailableError("Database is not configured (set DATABASE_URL)")
    return database


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    claims = decode_access_token(credentials.credentials, settings)
    user = await session.get(User, claims.sub)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid access token", headers={"WWW-Authenticate": "Bearer"})
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_principal(user: CurrentUserDep) -> Principal:
    # Permissions come from the user's *current* role in the database, so a role
    # change or deactivation takes effect immediately rather than at token expiry.
    return Principal.human(user.id, user.email, user.role)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_permissions(*required: Permission) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: PrincipalDep) -> Principal:
        missing = [p.value for p in required if not principal.has(p)]
        if missing:
            raise AuthorizationError(
                "You do not have permission to perform this action",
                details={"missing_permissions": missing},
            )
        return principal

    return dependency


def get_agent_models(request: Request) -> AgentModels:
    models: AgentModels | None = request.app.state.agent_models
    if models is None:
        raise ServiceUnavailableError(
            "No language model is configured (set LLM1_MODEL_NAME and LLM_BASE_URL)"
        )
    return models


def get_tool_registry(request: Request) -> ToolRegistry:
    return request.app.state.tool_registry


AgentModelsDep = Annotated[AgentModels, Depends(get_agent_models)]
ToolRegistryDep = Annotated[ToolRegistry, Depends(get_tool_registry)]
