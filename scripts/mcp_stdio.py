"""MCP server over stdio, for desktop clients that launch a local process.

Configure the client (e.g. Claude Desktop's ``mcpServers``) to run::

    python -m scripts.mcp_stdio

with environment ``SOVEREIGN_MCP_TOKEN=sov_pat_...`` (create one on the Assistant page)
plus the usual ``DATABASE_URL`` and integration settings from ``.env``. Messages are
newline-delimited JSON-RPC on stdin/stdout; logs go to stderr. The token is re-checked on
every tool call, so revoking it takes effect immediately.
"""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from backend.app.core.config import Settings
from backend.app.core.security.permissions import Principal
from backend.app.domains.identity.token_service import TokenService
from backend.app.domains.mcp.codemode import CodeMode
from backend.app.domains.mcp.server import McpError, McpServer, parse, parse_error_response
from backend.app.tools.gateway import ToolGateway


async def serve(
    read_line: Callable[[], Awaitable[str]],
    write_line: Callable[[str], None],
    settings: Settings,
    token: str,
    app_state: object,
) -> None:
    """The stdio loop; ``app_state`` carries ``database`` and ``tool_registry``."""
    database = app_state.database  # type: ignore[attr-defined]
    registry = app_state.tool_registry  # type: ignore[attr-defined]
    server = McpServer(settings.version)

    @asynccontextmanager
    async def open_codemode() -> AsyncIterator[CodeMode]:
        async with database.session() as session:
            user = await TokenService(session).authenticate(token)
            principal = Principal.human(user.id, user.email, user.role)
            yield CodeMode(ToolGateway(registry, session, settings), principal, settings)

    while line := await read_line():
        if not line.strip():
            continue
        try:
            response = await server.handle(parse(line), open_codemode)
        except McpError as exc:
            response = parse_error_response(exc)
        except Exception as exc:  # e.g. a revoked token: answer, keep serving
            message = getattr(exc, "message", None) or exc.__class__.__name__
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32603, "message": str(message)}}  # fmt: skip
        if response is not None:
            write_line(json.dumps(response, ensure_ascii=False))


def main() -> None:
    token = os.environ.get("SOVEREIGN_MCP_TOKEN", "")
    if not token:
        print("Set SOVEREIGN_MCP_TOKEN to a personal access token.", file=sys.stderr)
        sys.exit(2)
    settings = Settings(log_stream="stderr")

    from backend.app.main import create_app

    app = create_app(settings)  # same wiring as the API: database, integrations, tools

    async def read_line() -> str:
        return await asyncio.to_thread(sys.stdin.readline)

    def write_line(text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    asyncio.run(serve(read_line, write_line, settings, token, app.state))


if __name__ == "__main__":
    main()
