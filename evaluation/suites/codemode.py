"""MCP code mode: sandbox containment, governance of scripted tool calls, task success.

Runs scripts through the real ``CodeMode`` → sandbox → ``ToolGateway`` stack on the
seeded evaluation environment (demo integrations, in-memory DB).
"""

from sqlalchemy import select

from backend.app.core.security.permissions import Role
from backend.app.db.models import ApprovalRequest, ApprovalStatus, AuditEvent
from backend.app.domains.mcp.codemode import CodeMode
from backend.app.tools.gateway import ToolGateway
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment


async def run() -> SuiteResult:
    data = load_dataset("codemode")
    result = SuiteResult("codemode")
    limits = {"mcp_max_steps": 20_000, "mcp_code_timeout_seconds": 5, "mcp_max_tool_calls": 10}

    with Timer() as timer:
        async with environment(**limits) as env:

            async def execute(role: Role, code: str):
                async with env.database.session() as session:
                    gateway = ToolGateway(env.registry, session, env.settings)
                    return await CodeMode(gateway, env.principals[role], env.settings).execute(code)

            blocked = 0
            for case in data["escapes"]:
                outcome = await execute(Role.ADMIN, case["code"])
                ok = not outcome.ok and outcome.result is None
                blocked += ok
                result.cases.append(CaseResult(f"escape-{case['id']}", ok,
                                               {"error_type": outcome.error_type}))  # fmt: skip

            governance: list[tuple[str, bool, dict]] = []

            # A member may not use HR tools: they are absent from the script's namespace.
            outcome = await execute(Role.MEMBER, "keka.search_employees()")
            governance.append(("member-cannot-call-hr", outcome.error_type == "NameError", {}))

            # Auditors cannot drive the agent at all, so no tool namespaces exist for them.
            outcome = await execute(Role.AUDITOR, "jira.get_project(project_key='PHX')")
            governance.append(("auditor-has-no-tools", not outcome.ok, {}))

            # Writes never run directly: they become pending approvals.
            outcome = await execute(
                Role.TEAM_LEAD, "jira.add_comment(issue_key='PHX-1', comment='scripted')"
            )
            async with env.database.session() as session:
                approval = await session.scalar(
                    select(ApprovalRequest).where(ApprovalRequest.tool_name == "jira.add_comment")
                )
            held = (
                approval is not None
                and approval.status == ApprovalStatus.PENDING
                and outcome.result.get("status") == "pending_approval"
            )
            governance.append(("write-needs-approval", held, {}))

            # Every script run and every tool call is in the audit log.
            async with env.database.session() as session:
                actions_seen = set(await session.scalars(select(AuditEvent.action)))
            audited = {"mcp.execute", "tool.invoke", "approval.request"} <= actions_seen
            governance.append(
                ("runs-and-calls-audited", audited, {"actions": sorted(actions_seen)})
            )

            for case_id, passed, details in governance:
                result.cases.append(CaseResult(f"governance-{case_id}", passed, details))

            solved = 0
            for case in data["tasks"]:
                outcome = await execute(Role(case["role"]), case["code"])
                ok = outcome.ok and outcome.result == case["expect"]
                solved += ok
                result.cases.append(CaseResult(f"task-{case['id']}", ok,
                                               {"result": outcome.result, "error": outcome.error}))  # fmt: skip

    t = data["thresholds"]
    passed_governance = sum(1 for _, passed, _ in governance if passed)
    result.metrics = [
        Metric("escape_block_rate", ratio(blocked, len(data["escapes"])), t["escape_block_rate"]),
        Metric("governance_pass_rate", ratio(passed_governance, len(governance)),
               t["governance_pass_rate"]),
        Metric("task_success_rate", ratio(solved, len(data["tasks"])), t["task_success_rate"]),
    ]  # fmt: skip
    result.seconds = timer.seconds
    return result
