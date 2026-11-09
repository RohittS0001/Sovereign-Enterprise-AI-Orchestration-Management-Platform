"""Execute node: run the planned calls through the tool gateway."""

from backend.app.guardrails.injection import scan_for_injection
from backend.app.guardrails.redaction import redact_text
from backend.app.mcp.schemas.common import ToolStatus
from backend.app.orchestration.deps import AgentDeps
from backend.app.orchestration.observations import to_text
from backend.app.orchestration.state.state import AgentState, Observation

INJECTION_FLAG = "possible_prompt_injection"


async def execute_pending(state: AgentState, deps: AgentDeps) -> AgentState:
    risk_flags = list(state.get("risk_flags", []))
    observations: list[Observation] = []

    for step in state.get("pending", []):
        result = await deps.gateway.invoke(
            deps.agent,
            step.tool,
            step.arguments,
            run_id=deps.run_id,
            risk_flags=risk_flags,
        )

        content, suspicious = "", False
        if result.status in (ToolStatus.SUCCESS, ToolStatus.PENDING_APPROVAL):
            text = to_text(result.data, deps.settings.agent_observation_max_chars)
            content = redact_text(
                text, include_contact_info=deps.settings.guardrails_redact_contact_info
            ).text
            if result.status == ToolStatus.SUCCESS:
                suspicious = scan_for_injection(content).flagged
                if suspicious and INJECTION_FLAG not in risk_flags:
                    risk_flags.append(INJECTION_FLAG)

        observations.append(
            Observation(
                step_id=step.id,
                tool=step.tool,
                purpose=step.purpose,
                arguments=step.arguments,
                status=result.status.value,
                content=content,
                error=result.error,
                approval_id=result.approval_id,
                suspicious=suspicious,
            )
        )

    return {"observations": observations, "pending": [], "risk_flags": risk_flags}
