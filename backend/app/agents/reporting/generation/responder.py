"""Respond node: LLM 2 writes the grounded answer with citations."""

import re

from backend.app.agents.reporting.prompts.responder import RESPONDER_SYSTEM, RESPONDER_USER
from backend.app.guardrails.redaction import redact_text
from backend.app.llm.base import ChatMessage
from backend.app.orchestration.deps import AgentDeps
from backend.app.orchestration.observations import render_observations
from backend.app.orchestration.state.state import AgentState

_CITATION = re.compile(r"\[(S\d+)\]")


async def respond(state: AgentState, deps: AgentDeps) -> AgentState:
    observations = state.get("observations", [])
    verification = state.get("verification")
    notes = ""
    if verification and verification.notes:
        notes = "Notes:\n" + "\n".join(f"- {n}" for n in verification.notes) + "\n"

    query = redact_text(
        state["query"], include_contact_info=deps.settings.guardrails_redact_contact_info
    ).text
    messages = [
        ChatMessage("system", RESPONDER_SYSTEM.format(today=deps.today.isoformat())),
        ChatMessage(
            "user",
            RESPONDER_USER.format(
                query=query, observations=render_observations(observations), notes=notes
            ),
        ),
    ]
    response = await deps.models.reporting.chat(messages)
    deps.usage.add(response.prompt_tokens, response.completion_tokens)

    answer = response.content.strip()
    valid_ids = {o.step_id for o in observations if o.status == "success"}
    # Keep only citations that point at real, successful steps.
    citations = sorted(
        {c for c in _CITATION.findall(answer) if c in valid_ids},
        key=lambda c: int(c[1:]),
    )
    return {"answer": answer, "citations": citations}
