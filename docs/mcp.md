# MCP server (code mode)

Sovereign is an MCP server, so Claude Desktop, IDEs and other agents can use the platform's
governed tools (Jira, GitHub, HR, documents, analytics, and every connector you enable) with
the same permissions, approvals and audit trail as the built-in assistant.

## Why code mode

A classic MCP server lists one tool per operation, which means hundreds of schemas in the
client's context window, and every intermediate result passes back through the model. Code
mode exposes two tools instead:

| Tool | What it does |
|---|---|
| `search_tools(query, detail, limit)` | Returns the tools **you** may use as Python stubs (signature, parameters, description, read/write) |
| `execute_code(code)` | Runs a short Python script in which each tool is a function, such as `jira.get_issue(issue_key="PHX-1")` |

The client model writes one script that fetches, joins and filters data. Only the final answer
comes back, which saves tokens and latency and keeps raw records out of the client model.

```python
issues = jira.search_issues(jql="project = PHX AND status != Done")["issues"]
prs = github.list_pull_requests(repository="acme/payments", state="open")["pull_requests"]
linked = {p["title"].split()[0] for p in prs}
[{"key": i["key"], "has_pr": i["key"] in linked} for i in issues if i["priority"] == "High"]
```

## Security model

- **Identity.** The client authenticates with a personal access token (`sov_pat_…`) that you
  create on the **MCP access** page. Only a SHA-256 hash is stored. Tokens expire (at most
  `MCP_TOKEN_MAX_DAYS`), can be revoked at any time, and are accepted only on `/mcp`, not on
  the REST API.
- **Permissions.** Scripts act as `principal.as_agent()`, which is your current role's
  permissions intersected with the agent scopes. MCP needs `agent:query`, as the assistant
  does. Tools you can't use don't exist in the script's namespace.
- **Governance.** Every call goes through `ToolGateway` (permission → validation → approval →
  audit). Writes return `{"status": "pending_approval"}` and run only after a person approves
  them. Every script is audited as `mcp.execute` (with a code hash, tool calls and outcome),
  and every tool call as `tool.invoke`.
- **Data handling.** Tool results are redacted (secrets, cards, IDs) before the script sees
  them. The final output is injection-scanned, and a warning is attached if it contains
  instruction-like text.
- **Sandbox.** `backend/app/domains/mcp/sandbox.py` interprets a Python subset node by node;
  model-written code is never `exec`'d. The script has no imports, classes, files or network,
  no dunder names or attributes, and attributes are limited to whitelisted methods of plain
  data types. Steps, wall time, tool calls, recursion depth, output, string, collection and
  integer sizes are all capped (`MCP_*` settings), and a script can't catch limit errors.
  The `codemode` evaluation suite runs 20 escape attempts in CI, and every one must be
  blocked.
- **Transport.** Requests are rate-limited per user (`MCP_RATE_LIMIT_PER_MINUTE`). The
  endpoint rejects browser origins outside `CORS_ORIGINS` (DNS-rebinding protection) and
  unknown `MCP-Protocol-Version` headers.

The sandbox is a pure-Python interpreter so it runs anywhere, including this project's
Windows development machine, where Application Control blocks native interpreters such as
Monty or QuickJS. Where native code is allowed, a WebAssembly or Monty backend could replace
it behind the same `run()` interface.

## Connecting a client

**HTTP (Streamable HTTP, stateless JSON responses)**

```
URL:    https://<your-host>/mcp
Header: Authorization: Bearer sov_pat_...
```

**stdio (a local process).** Example Claude Desktop config:

```json
{
  "mcpServers": {
    "sovereign": {
      "command": "C:/path/to/repo/.venv/Scripts/python.exe",
      "args": ["-m", "scripts.mcp_stdio"],
      "cwd": "C:/path/to/repo",
      "env": { "SOVEREIGN_MCP_TOKEN": "sov_pat_..." }
    }
  }
}
```

The stdio server reads `DATABASE_URL` and integration settings from `.env`, logs to stderr,
and re-checks the token on every call, so revoking it takes effect immediately.

## Protocol support

JSON-RPC 2.0, implemented in `backend/app/domains/mcp/server.py` with no SDK dependency:
`initialize` (negotiates 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05),
`notifications/initialized`, `ping`, `tools/list` and `tools/call` (text content plus
`structuredContent`). The server is stateless: no session IDs and no server-initiated
messages (`GET /mcp` returns 405).
