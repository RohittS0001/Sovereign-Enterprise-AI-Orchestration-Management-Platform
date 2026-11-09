"""MCP Streamable HTTP transport (stateless, JSON responses) at ``/mcp``.

Clients authenticate with ``Authorization: Bearer <personal access token>``; a normal
access token also works (handy for the browser and tests). Each request is rate-limited
per user and runs with the user's agent permissions through the tool gateway.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.api.deps import (
    DatabaseDep,
    RateLimiterDep,
    SettingsDep,
    ToolRegistryDep,
)
from backend.app.core.config import Settings
from backend.app.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from backend.app.core.security.permissions import Permission, Principal
from backend.app.core.security.tokens import decode_access_token
from backend.app.db.models import User
from backend.app.domains.identity.token_service import TOKEN_PREFIX, TokenService
from backend.app.domains.mcp.codemode import CodeMode
from backend.app.domains.mcp.server import (
    PROTOCOL_VERSIONS,
    McpError,
    McpServer,
    parse,
    parse_error_response,
)
from backend.app.tools.gateway import ToolGateway

router = APIRouter(tags=["MCP"])
_bearer = HTTPBearer(auto_error=False, description="Personal access token (sov_pat_...)")


async def authenticate(database, settings: Settings, token: str) -> User:
    async with database.session() as session:
        if token.startswith(TOKEN_PREFIX):
            return await TokenService(session).authenticate(token)
        claims = decode_access_token(token, settings)
        user = await session.get(User, claims.sub)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid access token")
        return user


def check_origin(request: Request, settings: Settings) -> None:
    # Browsers send Origin; reject cross-site pages (DNS-rebinding protection, MCP spec).
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins:
        raise AuthorizationError("Origin not allowed")


@router.post("/mcp")
async def mcp_post(
    request: Request,
    settings: SettingsDep,
    database: DatabaseDep,
    registry: ToolRegistryDep,
    limiter: RateLimiterDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Response:
    if not settings.mcp_enabled:
        raise NotFoundError("MCP is disabled")
    check_origin(request, settings)
    version = request.headers.get("mcp-protocol-version")
    if version and version not in PROTOCOL_VERSIONS:
        return JSONResponse(
            parse_error_response(McpError(-32600, f"unsupported MCP-Protocol-Version {version}")),
            status_code=400,
        )
    if credentials is None:
        raise AuthenticationError("Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    user = await authenticate(database, settings, credentials.credentials)
    await limiter.hit(f"mcp:{user.id}", limit=settings.mcp_rate_limit_per_minute, window_seconds=60)
    principal = Principal.human(user.id, user.email, user.role)
    if not principal.has(Permission.AGENT_QUERY):
        raise AuthorizationError("MCP access needs the agent:query permission")

    try:
        message = parse(await request.body())
    except McpError as exc:
        return JSONResponse(parse_error_response(exc), status_code=400)

    @asynccontextmanager
    async def open_codemode() -> AsyncIterator[CodeMode]:
        async with database.session() as session:
            yield CodeMode(ToolGateway(registry, session, settings), principal, settings)

    response = await McpServer(settings.version).handle(message, open_codemode)
    if response is None:
        return Response(status_code=202)  # notifications and responses get no body
    return JSONResponse(response)


@router.get("/mcp")
async def mcp_get() -> Response:
    # No server-initiated stream: this server only answers requests.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})
