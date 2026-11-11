"""Chat front-ends (Slack, Teams): run the agent as the person who asked.

A chat user is mapped to a platform user by email. Unknown, inactive or under-privileged
people get a polite refusal; everyone else gets exactly what the web assistant would give
them (same permissions, approvals, audit, quota and rate limit).
"""

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.errors import AppException
from backend.app.core.logging import get_logger
from backend.app.core.security.permissions import Permission, Principal
from backend.app.core.security.rate_limit import RateLimiter, RedisRateLimiter
from backend.app.db.models import User
from backend.app.db.session import Database
from backend.app.domains.agent.service import AgentService, run_to_response
from backend.app.domains.governance.approvals.service import ApprovalService
from backend.app.domains.usage.service import enforce_daily_quota
from backend.app.llm.base import AgentModels
from backend.app.tools.registry import ToolRegistry

logger = get_logger(__name__)

MAX_SIGNATURE_AGE_SECONDS = 300


def verify_slack_signature(
    secret: str, timestamp: str, signature: str, body: bytes, now: float | None = None
) -> bool:
    """Slack's v0 request signing: HMAC-SHA256 of ``v0:{timestamp}:{body}``, max 5 minutes old."""
    if not (secret and timestamp.isdigit() and signature.startswith("v0=")):
        return False
    if abs((now or time.time()) - int(timestamp)) > MAX_SIGNATURE_AGE_SECONDS:
        return False  # replayed or stale request
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body,
                                hashlib.sha256).hexdigest()  # fmt: skip
    return hmac.compare_digest(expected, signature)


@dataclass
class ChatAnswer:
    text: str
    pending_approvals: list[str] = field(default_factory=list)
    refused: bool = False


@dataclass
class ChatContext:
    database: Database
    settings: Settings
    models: AgentModels | None
    registry: ToolRegistry
    limiter: RateLimiter | RedisRateLimiter


async def principal_for(context: ChatContext, email: str | None) -> Principal | None:
    if not email:
        return None
    async with context.database.session() as session:
        user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        return None
    return Principal.human(user.id, user.email, user.role)


async def answer_as(context: ChatContext, email: str | None, question: str) -> ChatAnswer:
    """Run the agent for a chat message as the matching platform user."""
    question = question.strip()[:4000]
    principal = await principal_for(context, email)
    if principal is None:
        return ChatAnswer("I don't recognise you as a platform user (matched by email). "
                          "Ask an admin to add you.", refused=True)  # fmt: skip
    if not principal.has(Permission.AGENT_QUERY):
        return ChatAnswer("Your role cannot use the assistant.", refused=True)
    if context.models is None:
        return ChatAnswer("The assistant is not configured (no language model).", refused=True)
    if not question:
        return ChatAnswer("Ask me something about your projects.", refused=True)
    settings = context.settings
    try:
        await context.limiter.hit(
            f"agent:user:{principal.user_id}",
            limit=settings.agent_rate_limit_per_minute,
            window_seconds=60,
        )
        async with context.database.session() as session:
            await enforce_daily_quota(session, principal.user_id, settings.agent_daily_token_quota)
            run = await AgentService(session, settings, context.models, context.registry).run(
                principal, question
            )
            response = run_to_response(run)
    except AppException as exc:
        return ChatAnswer(exc.message, refused=True)
    pending = [str(a) for a in response.pending_approvals]
    text = response.answer or response.error or "I could not produce an answer."
    if pending:
        text += f"\n\n{len(pending)} action(s) need approval before they run."
    return ChatAnswer(text, pending)


async def decide_as(
    context: ChatContext, email: str | None, approval_id: str, approve: bool
) -> str:
    """Approve/reject from chat with the clicker's own permissions (four-eyes etc. apply)."""
    principal = await principal_for(context, email)
    if principal is None:
        return "I don't recognise you as a platform user."
    try:
        async with context.database.session() as session:
            approval = await ApprovalService(session, context.settings, context.registry).decide(
                uuid.UUID(approval_id), principal, approve=approve, note="via chat")  # fmt: skip
            return f"{approval.tool_name}: {approval.status.value}"
    except (AppException, ValueError) as exc:
        return getattr(exc, "message", None) or str(exc)


def _button(label: str, action: str, style: str, approval_id: str) -> dict[str, Any]:
    return {"type": "button", "style": style, "text": {"type": "plain_text", "text": label},
            "action_id": action, "value": approval_id}  # fmt: skip


def approval_blocks(text: str, pending: list[str]) -> list[dict[str, Any]]:
    """Slack Block Kit: the answer plus Approve/Reject buttons per pending action."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}
    ]
    for approval_id in pending[:5]:
        blocks.append({"type": "actions", "block_id": f"approval:{approval_id}", "elements": [
            _button("Approve", "approve", "primary", approval_id),
            _button("Reject", "reject", "danger", approval_id),
        ]})  # fmt: skip
    return blocks
