"""Code mode: governed tools as a typed Python API that client models script against.

Instead of one MCP tool per platform tool (hundreds of schemas in the client's context,
every intermediate result round-tripping through the model), clients get two tools:

- ``search_tools`` — discover the tools *this user* may use, as Python stubs;
- ``execute_code`` — run a short script in the sandbox (``sandbox.py``) where each tool is
  a function such as ``jira.get_issue(issue_key="PHX-1")``.

Every function call goes through ``ToolGateway`` as the token owner's *agent* principal
(user permissions ∩ AGENT_SCOPES): permission → validation → approval → audit, exactly as
for the built-in agent. Writes become pending approvals; tool results are redacted before
the script sees them; the final output is injection-scanned and the run is audited.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.security.permissions import Permission, Principal
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.governance.guardrails.injection import scan_for_injection
from backend.app.domains.governance.guardrails.redaction import redact_data
from backend.app.domains.mcp.sandbox import (
    HostFunction,
    LimitExceeded,
    Limits,
    Namespace,
    SandboxException,
    run,
    to_plain,
)
from backend.app.tools.base import ToolStatus
from backend.app.tools.gateway import ToolGateway
from backend.app.tools.registry import ToolSpec

_JSON_TYPES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool",
               "object": "dict", "null": "None"}  # fmt: skip


def _hint(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in schema:
        return _hint(defs.get(schema["$ref"].split("/")[-1], {}), defs)
    if "enum" in schema:
        return "Literal[" + ", ".join(repr(v) for v in schema["enum"]) + "]"
    if "const" in schema:
        return f"Literal[{schema['const']!r}]"
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        return " | ".join(dict.fromkeys(_hint(o, defs) for o in options))
    kind = schema.get("type")
    if kind == "array":
        return f"list[{_hint(schema.get('items', {}), defs)}]"
    if kind == "string" and schema.get("format") in {"date", "date-time"}:
        return "str"  # ISO 8601
    return _JSON_TYPES.get(kind, "Any") if isinstance(kind, str) else "Any"


def tool_stub(spec: ToolSpec, *, with_schema: bool = False) -> str:
    """A Python signature + docstring for one tool, as the client model will call it."""
    schema = spec.input_model.model_json_schema() if spec.input_model else {}
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    params = []
    for name, prop in schema.get("properties", {}).items():
        hint = _hint(prop, defs)
        params.append(f"{name}: {hint}" if name in required else
                      f"{name}: {hint} = {prop.get('default')!r}")  # fmt: skip
    access = "WRITE: returns a pending approval until a human approves" if spec.is_write else "read"
    signature = f"{spec.name}({'*, ' if params else ''}{', '.join(params)}) -> dict"
    doc = f'{signature}\n    """{spec.description} [{access}]"""'
    if with_schema and schema:
        doc += f"\n    # input schema: {schema}"
    return doc


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


@dataclass
class ExecutionResult:
    ok: bool
    result: Any = None
    stdout: str = ""
    error: str | None = None
    error_type: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_approvals: list[str] = field(default_factory=list)
    injection_warning: list[str] = field(default_factory=list)
    output_truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "result": self.result, "stdout": self.stdout, "error": self.error,
            "error_type": self.error_type, "tool_calls": self.tool_calls,
            "pending_approvals": self.pending_approvals,
            "injection_warning": self.injection_warning,
            "output_truncated": self.output_truncated,
        }  # fmt: skip


GUIDE = """\
Write Python (a safe subset) that calls the platform's governed tools as functions and
returns only what you need. Rules:
- Tools are `namespace.function(**kwargs)`, e.g. `jira.get_issue(issue_key="PHX-1")`;
  use search_tools first to see what exists for you and their exact parameters.
- The value of the last expression (or a variable named `result`) is returned; `print`
  output is returned too. Filter and aggregate in code instead of returning raw data.
- No imports, classes, files or network. Available helpers: len, range, min, max, sum,
  sorted, enumerate, zip, any, all, map, filter, isinstance, round, abs, json.dumps/loads,
  math.*, statistics.mean/median/pstdev, dates.today/days_between/add_days.
- A failed or denied tool call raises ToolError (catch it with try/except ToolError).
- Write tools do not run immediately: they return {"status": "pending_approval", ...}
  and a human approves them in the platform.
"""


class CodeMode:
    """Code-mode access to the gateway for one authenticated principal."""

    def __init__(self, gateway: ToolGateway, principal: Principal, settings: Settings) -> None:
        self.gateway = gateway
        # Like the built-in agent, code mode needs agent:query (auditors etc. get no tools).
        self.allowed = principal.has(Permission.AGENT_QUERY)
        self.principal = principal.as_agent()  # never more than the token owner may do
        self.settings = settings
        self.limits = Limits(
            max_steps=settings.mcp_max_steps,
            timeout_seconds=settings.mcp_code_timeout_seconds,
            max_tool_calls=settings.mcp_max_tool_calls,
            max_output_chars=settings.mcp_max_output_chars,
        )

    def tools(self) -> list[ToolSpec]:
        return self.gateway.available_tools(self.principal) if self.allowed else []

    def search(self, query: str = "", *, detail: str = "signatures", limit: int = 20) -> str:
        """Matching tools as Python stubs (or just names), best match first."""
        specs = self.tools()
        wanted = _words(query)
        if wanted:
            scored = [(len(wanted & _words(f"{s.name} {s.description}")), s) for s in specs]
            specs = [s for score, s in sorted(scored, key=lambda p: (-p[0], p[1].name)) if score]
        specs = specs[: max(1, min(limit, 100))]
        if not specs:
            return "No matching tools. Call search_tools with an empty query to list all."
        if detail == "names":
            return "\n".join(f"{s.name}  [{'write' if s.is_write else 'read'}]" for s in specs)
        return "\n\n".join(tool_stub(s, with_schema=detail == "full") for s in specs)

    def _namespaces(self, calls: list[dict[str, Any]], pending: list[str]) -> dict[str, Any]:
        namespaces: dict[str, dict[str, Any]] = {}
        for spec in self.tools():
            prefix, _, function = spec.name.partition(".")
            namespaces.setdefault(prefix, {})[function] = HostFunction(
                spec.name, self._tool_function(spec.name, calls, pending)
            )
        return {name: Namespace(name, members) for name, members in namespaces.items()}

    def _tool_function(self, name: str, calls: list[dict[str, Any]], pending: list[str]) -> Any:
        async def call(*args: Any, **kwargs: Any) -> Any:
            if args and not (len(args) == 1 and isinstance(args[0], dict)):
                raise SandboxException("TypeError", f"{name}() takes keyword arguments")
            if len(calls) >= self.limits.max_tool_calls:
                raise LimitExceeded(f"more than {self.limits.max_tool_calls} tool calls")
            payload = {**(args[0] if args else {}), **kwargs}
            result = await self.gateway.invoke(self.principal, name, payload)
            calls.append({"tool": name, "status": result.status.value})
            if result.status == ToolStatus.SUCCESS:
                return to_plain(redact_data(
                    result.data,
                    include_contact_info=self.settings.guardrails_redact_contact_info,
                ))  # fmt: skip
            if result.status == ToolStatus.PENDING_APPROVAL:
                pending.append(result.approval_id or "")
                return {
                    "status": "pending_approval",
                    "approval_id": result.approval_id,
                    "message": "Not executed yet: a person must approve it in the platform.",
                }
            raise SandboxException("ToolError", f"{name}: {result.error or result.status.value}")

        return call

    async def execute(self, code: str) -> ExecutionResult:
        calls: list[dict[str, Any]] = []
        pending: list[str] = []
        outcome = await run(code, self._namespaces(calls, pending), self.limits)
        result = ExecutionResult(
            ok=outcome.ok, result=outcome.result, stdout=outcome.stdout, error=outcome.error,
            error_type=outcome.error_type, tool_calls=calls, pending_approvals=pending,
            output_truncated=outcome.output_truncated,
        )  # fmt: skip
        scan = scan_for_injection(f"{outcome.stdout}\n{outcome.result!r}")
        result.injection_warning = scan.matches
        await AuditService(self.gateway.session).record(
            actions.MCP_EXECUTE,
            status=actions.SUCCESS if outcome.ok else actions.FAILURE,
            principal=self.principal,
            resource_type="mcp",
            details={
                "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "code_chars": len(code),
                "steps": outcome.steps,
                "tool_calls": calls,
                "error_type": outcome.error_type,
                "injection_warning": scan.matches,
            },
        )
        await self.gateway.session.commit()
        return result
