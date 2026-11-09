"""Verify node: deterministic checks on what the run actually achieved."""

from backend.app.orchestration.state.state import AgentState, Verification


def verify_run(state: AgentState) -> AgentState:
    observations = state.get("observations", [])

    failed = [o.step_id for o in observations if o.status == "error"]
    denied = [o.step_id for o in observations if o.status == "denied"]
    pending = [
        o.approval_id for o in observations if o.status == "pending_approval" and o.approval_id
    ]
    succeeded = [o for o in observations if o.status == "success"]

    notes: list[str] = []
    if failed:
        notes.append(f"Steps {', '.join(failed)} failed; do not present their data as known.")
    if denied:
        notes.append(f"Steps {', '.join(denied)} were denied by access control.")
    if pending:
        notes.append(f"{len(pending)} action(s) are awaiting human approval and have NOT run yet.")
    if any(o.suspicious for o in observations):
        notes.append("Some retrieved content looked like a prompt-injection attempt.")
    if observations and not succeeded and not pending:
        notes.append("No tool call succeeded, so no organizational data is available.")

    ok = not failed and not denied and (bool(succeeded) or bool(pending) or not observations)
    return {
        "verification": Verification(
            ok=ok,
            failed_steps=failed,
            denied_steps=denied,
            pending_approvals=pending,
            notes=notes,
        )
    }
