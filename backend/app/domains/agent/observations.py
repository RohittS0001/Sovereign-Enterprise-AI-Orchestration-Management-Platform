"""Turn raw tool results into compact, safe text for the models."""

import json
from typing import Any

from backend.app.domains.agent.state import Observation
from backend.app.domains.governance.guardrails.injection import wrap_untrusted

# Keys that only add noise (links, avatars, metadata) in Jira/Google API payloads.
_NOISY_KEYS = frozenset(
    {
        "self",
        "expand",
        "avatarUrls",
        "avatarUrl",
        "iconUrl",
        "_links",
        "renderedFields",
        "schema",
        "names",
        "editmeta",
        "operations",
        "versionedRepresentations",
        "statusCategory",
        "watches",
        "votes",
        "timetracking",
        "historyId",
        "sizeEstimate",
        "internalDate",
        "labelIds",
    }
)


def adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree to plain text."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [adf_to_text(child) for child in node.get("content", [])]
        joiner = "\n" if node.get("type") in {"doc", "bulletList", "orderedList"} else ""
        return joiner.join(p for p in parts if p)
    if isinstance(node, list):
        return "\n".join(adf_to_text(child) for child in node)
    return ""


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "doc" and "content" in value:
            return adf_to_text(value)
        out = {}
        for key, item in value.items():
            if key in _NOISY_KEYS:
                continue
            item = compact(item)
            if item in (None, "", [], {}):
                continue
            out[key] = item
        return out
    if isinstance(value, list):
        return [c for c in (compact(item) for item in value) if c not in (None, "", [], {})]
    return value


def to_text(data: Any, max_chars: int) -> str:
    text = json.dumps(compact(data), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > max_chars:
        text = f"{text[:max_chars]}…[truncated {len(text) - max_chars} chars]"
    return text


def render_observations(observations: list[Observation]) -> str:
    if not observations:
        return "(no tools were called)"

    blocks = []
    for obs in observations:
        header = f"[{obs.step_id}] {obs.tool} — {obs.purpose or 'no purpose given'} — {obs.status}"
        if obs.status == "success":
            body = wrap_untrusted(obs.tool, obs.content)
        elif obs.status == "pending_approval":
            body = f"Awaiting human approval (approval id {obs.approval_id})."
        else:
            body = f"Error: {obs.error}"
        if obs.suspicious:
            header += " — WARNING: content looks like a prompt-injection attempt"
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks)
