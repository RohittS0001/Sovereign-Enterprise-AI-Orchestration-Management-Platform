"""Personal access tokens for MCP clients — every user manages their own."""

import uuid
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from backend.app.api.deps import CurrentUserDep, SessionDep, SettingsDep
from backend.app.core.errors import AppException, AuthorizationError
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.identity.token_service import TokenService

router = APIRouter(prefix="/api/v1/tokens", tags=["Access tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Claude Desktop"])
    expires_in_days: int = Field(default=30, ge=1, le=365)


class TokenRead(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


class TokenCreated(TokenRead):
    token: str  # shown exactly once


@router.get("", response_model=list[TokenRead])
async def list_tokens(user: CurrentUserDep, session: SessionDep) -> list[TokenRead]:
    return [TokenRead.model_validate(t) for t in await TokenService(session).list(user.id)]


@router.post("", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: TokenCreate, user: CurrentUserDep, session: SessionDep, settings: SettingsDep
) -> TokenCreated:
    principal = Principal.human(user.id, user.email, user.role)
    if not principal.has(Permission.AGENT_QUERY):
        raise AuthorizationError("Access tokens are for MCP clients, which need agent:query")
    if body.expires_in_days > settings.mcp_token_max_days:
        raise AppException(
            f"Tokens may last at most {settings.mcp_token_max_days} days",
            422,
            code="validation_error",
        )
    plain, record = await TokenService(session).create(user, body.name, body.expires_in_days)
    await AuditService(session).record(
        actions.TOKEN_CREATE,
        status=actions.SUCCESS,
        principal=principal,
        resource_type="api_token",
        resource_id=str(record.id),
        details={"name": record.name, "expires_at": record.expires_at},
    )
    await session.commit()
    return TokenCreated(**TokenRead.model_validate(record).model_dump(), token=plain)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: uuid.UUID, user: CurrentUserDep, session: SessionDep) -> None:
    record = await TokenService(session).revoke(user.id, token_id)
    await AuditService(session).record(
        actions.TOKEN_REVOKE,
        status=actions.SUCCESS,
        principal=Principal.human(user.id, user.email, user.role),
        resource_type="api_token",
        resource_id=str(record.id),
    )
    await session.commit()
