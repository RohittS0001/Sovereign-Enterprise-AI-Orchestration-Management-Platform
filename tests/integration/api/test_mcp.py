"""MCP server (code mode) over HTTP and stdio, with personal access tokens."""

import json

import pytest
from sqlalchemy import select

from backend.app.core.security.permissions import Role
from backend.app.db.models import ApprovalRequest, ApprovalStatus, AuditEvent
from backend.app.main import create_app
from scripts.mcp_stdio import serve as serve_stdio
from tests.conftest import serve

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def demo_settings(test_settings):
    return test_settings.model_copy(update={"demo_mode": True})


@pytest.fixture
async def mcp(demo_settings, database):
    async with serve(create_app(demo_settings, database)) as client:
        yield client


async def new_token(client, headers, name="Claude Desktop") -> str:
    response = await client.post("/api/v1/tokens", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["token"]


def rpc(method: str, params: dict | None = None, request_id: int | None = 1) -> dict:
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        message["id"] = request_id
    return message


async def call(client, token: str, message: dict, **headers):
    return await client.post(
        "/mcp", json=message, headers={"Authorization": f"Bearer {token}", **headers}
    )


async def execute(client, token: str, code: str) -> dict:
    response = await call(
        client, token, rpc("tools/call", {"name": "execute_code", "arguments": {"code": code}})
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


# ------------------------------------------------------------------ tokens


async def test_token_lifecycle_and_scope(mcp, make_user, auth_headers) -> None:
    headers = auth_headers(await make_user(role=Role.TEAM_LEAD))
    token = await new_token(mcp, headers)

    listed = (await mcp.get("/api/v1/tokens", headers=headers)).json()
    assert token.startswith("sov_pat_") and listed[0]["prefix"] == token[:14]
    assert "token" not in listed[0]  # the secret is shown only once
    assert (await call(mcp, token, rpc("ping"))).json()["result"] == {}

    # A token is for MCP only, not the regular API.
    assert (
        await mcp.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401

    assert (
        await mcp.delete(f"/api/v1/tokens/{listed[0]['id']}", headers=headers)
    ).status_code == 204
    assert (await call(mcp, token, rpc("ping"))).status_code == 401
    assert (await mcp.get("/api/v1/tokens", headers=headers)).json() == []


async def test_token_expiry_is_capped(mcp, make_user, auth_headers) -> None:
    headers = auth_headers(await make_user())
    response = await mcp.post(
        "/api/v1/tokens", json={"name": "x", "expires_in_days": 365}, headers=headers
    )
    assert response.status_code == 422


async def test_users_cannot_revoke_each_others_tokens(mcp, make_user, auth_headers) -> None:
    alice, bob = auth_headers(await make_user()), auth_headers(await make_user())
    await new_token(mcp, alice)
    token_id = (await mcp.get("/api/v1/tokens", headers=alice)).json()[0]["id"]

    assert (await mcp.delete(f"/api/v1/tokens/{token_id}", headers=bob)).status_code == 404


# ------------------------------------------------------------------ protocol


async def test_initialize_negotiates_the_protocol_version(mcp, make_user, auth_headers) -> None:
    token = await new_token(mcp, auth_headers(await make_user()))

    known = (await call(mcp, token, rpc("initialize", {"protocolVersion": "2025-06-18"}))).json()[
        "result"
    ]
    unknown = (await call(mcp, token, rpc("initialize", {"protocolVersion": "1999-01-01"}))).json()[
        "result"
    ]

    assert known["protocolVersion"] == "2025-06-18"
    assert unknown["protocolVersion"] == "2025-11-25"
    assert known["capabilities"] == {"tools": {"listChanged": False}}
    assert known["serverInfo"]["name"] == "sovereign"


async def test_tools_list_is_code_mode(mcp, make_user, auth_headers) -> None:
    token = await new_token(mcp, auth_headers(await make_user()))

    tools = (await call(mcp, token, rpc("tools/list"))).json()["result"]["tools"]

    assert [t["name"] for t in tools] == ["search_tools", "execute_code"]


async def test_protocol_errors(mcp, make_user, auth_headers) -> None:
    token = await new_token(mcp, auth_headers(await make_user()))

    assert (await call(mcp, token, rpc("nope"))).json()["error"]["code"] == -32601
    assert (
        await call(mcp, token, rpc("notifications/initialized", request_id=None))
    ).status_code == 202
    bad_json = await mcp.post(
        "/mcp", content=b"{not json", headers={"Authorization": f"Bearer {token}"}
    )
    assert bad_json.status_code == 400 and bad_json.json()["error"]["code"] == -32700
    batch = await mcp.post("/mcp", json=[rpc("ping")], headers={"Authorization": f"Bearer {token}"})
    assert batch.json()["error"]["code"] == -32600
    old = await call(mcp, token, rpc("ping"), **{"MCP-Protocol-Version": "2000-01-01"})
    assert old.status_code == 400
    assert (await mcp.post("/mcp", json=rpc("ping"))).status_code == 401
    evil = await call(mcp, token, rpc("ping"), Origin="https://evil.example.com")
    assert evil.status_code == 403
    assert (await mcp.get("/mcp")).status_code == 405


async def test_rate_limit(demo_settings, database, make_user, auth_headers) -> None:
    settings = demo_settings.model_copy(update={"mcp_rate_limit_per_minute": 2})
    async with serve(create_app(settings, database)) as client:
        token = await new_token(client, auth_headers(await make_user()))
        codes = [(await call(client, token, rpc("ping"))).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ------------------------------------------------------------------ code mode


async def test_search_tools_shows_only_what_the_user_may_use(mcp, make_user, auth_headers) -> None:
    member = await new_token(mcp, auth_headers(await make_user(role=Role.MEMBER)), "m")
    lead = await new_token(mcp, auth_headers(await make_user(role=Role.TEAM_LEAD)), "l")

    async def search(token, query=""):
        params = {
            "name": "search_tools",
            "arguments": {"query": query, "detail": "names", "limit": 100},
        }
        return (await call(mcp, token, rpc("tools/call", params))).json()["result"]["content"][0][
            "text"
        ]

    assert "keka.get_leave_requests" in await search(lead)
    assert "keka." not in await search(member)  # people:read is not a member permission
    stub = (
        await call(
            mcp,
            lead,
            rpc(
                "tools/call",
                {"name": "search_tools", "arguments": {"query": "jira issue comments"}},
            ),
        )
    ).json()
    text = stub["result"]["content"][0]["text"]
    assert "jira.get_issue_comments(*, issue_key: str" in text and "[read]" in text


async def test_execute_code_reads_filters_and_audits(
    mcp, database, make_user, auth_headers
) -> None:
    token = await new_token(mcp, auth_headers(await make_user(role=Role.TEAM_LEAD)))
    code = """
issues = jira.search_issues(jql="project = PHX AND status != Done")["issues"]
blocked = [i["key"] for i in issues if i.get("status") == "Blocked"]
print(len(issues), "open")
{"open": len(issues), "blocked": blocked}
"""
    result = await execute(mcp, token, code)

    data = result["structuredContent"]
    assert result["isError"] is False and data["ok"] is True
    assert data["result"]["open"] > 0 and data["stdout"].endswith("open\n")
    assert data["tool_calls"] == [{"tool": "jira.search_issues", "status": "success"}]
    async with database.session() as session:
        events = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action.in_(["mcp.execute", "tool.invoke"]))
            )
        )
    assert {e.action for e in events} == {"mcp.execute", "tool.invoke"}
    executed = next(e for e in events if e.action == "mcp.execute")
    assert executed.actor_type == "agent" and len(executed.details["code_sha256"]) == 64


async def test_writes_become_pending_approvals(mcp, database, make_user, auth_headers) -> None:
    token = await new_token(mcp, auth_headers(await make_user(role=Role.TEAM_LEAD)))

    result = await execute(mcp, token, 'jira.add_comment(issue_key="PHX-1", comment="from MCP")')

    data = result["structuredContent"]
    assert data["result"]["status"] == "pending_approval" and len(data["pending_approvals"]) == 1
    assert "await human approval" in result["content"][0]["text"]
    async with database.session() as session:
        approval = await session.scalar(select(ApprovalRequest))
    assert approval.status == ApprovalStatus.PENDING and approval.tool_name == "jira.add_comment"


async def test_tools_outside_the_users_permissions_do_not_exist(
    mcp, make_user, auth_headers
) -> None:
    token = await new_token(mcp, auth_headers(await make_user(role=Role.MEMBER)))

    result = await execute(mcp, token, "keka.search_employees()")

    assert result["isError"] is True
    assert result["structuredContent"]["error_type"] == "NameError"


async def test_tool_errors_are_catchable_and_sandbox_limits_apply(
    mcp, make_user, auth_headers
) -> None:
    token = await new_token(mcp, auth_headers(await make_user(role=Role.TEAM_LEAD)))
    code = """
try:
    jira.get_issue(issue_key="PHX-999")
except ToolError as e:
    result = "caught: " + e.message[:20]
"""
    assert (await execute(mcp, token, code))["structuredContent"]["result"].startswith("caught: ")
    escape = await execute(mcp, token, "().__class__.__bases__")
    assert escape["structuredContent"]["error_type"] == "SyntaxError"


# ------------------------------------------------------------------ stdio


async def test_stdio_transport(demo_settings, database, make_user, auth_headers) -> None:
    app = create_app(demo_settings, database)
    async with serve(app) as client:
        token = await new_token(client, auth_headers(await make_user(role=Role.TEAM_LEAD)))
    lines = [json.dumps(rpc("initialize", {"protocolVersion": "2025-06-18"})),
             json.dumps(rpc("notifications/initialized", request_id=None)),
             "{broken",
             json.dumps(rpc("tools/call", {"name": "execute_code", "arguments": {"code": "1 + 1"}}, 2)),
             ""]  # fmt: skip
    written: list[str] = []

    async def read_line() -> str:
        return lines.pop(0)

    await serve_stdio(read_line, written.append, demo_settings, token, app.state)

    responses = [json.loads(w) for w in written]
    assert [r.get("id") for r in responses] == [1, None, 2]
    assert responses[1]["error"]["code"] == -32700
    assert responses[2]["result"]["structuredContent"]["result"] == 2


async def test_mcp_needs_agent_query_like_the_builtin_agent(
    mcp, database, make_user, auth_headers
) -> None:
    auditor = await make_user(role=Role.AUDITOR)
    headers = auth_headers(auditor)

    assert (
        await mcp.post("/api/v1/tokens", json={"name": "x"}, headers=headers)
    ).status_code == 403
    # Even a normal access token cannot reach code mode without agent:query.
    access = headers["Authorization"].removeprefix("Bearer ")
    assert (await call(mcp, access, rpc("tools/list"))).status_code == 403
