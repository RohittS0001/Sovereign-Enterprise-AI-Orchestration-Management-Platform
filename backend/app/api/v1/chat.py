"""Inbound chat: Slack Events API + interactivity, Microsoft Teams outgoing webhook.

Every request is signature-verified before anything is parsed. Work (the agent run) happens
in a background task so Slack's 3-second / Teams' 5-second deadlines are met; answers are
posted back through the chat connector.
"""

import asyncio
import json
import re
from collections import deque
from collections.abc import Coroutine
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from backend.app.core.errors import AuthenticationError, NotFoundError
from backend.app.core.logging import get_logger
from backend.app.domains.chat.service import (
    ChatContext,
    answer_as,
    approval_blocks,
    decide_as,
    verify_slack_signature,
)
from backend.app.integrations.connectors.chat import verify_teams_hmac

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
logger = get_logger(__name__)
_MENTION = re.compile(r"<@[A-Z0-9]+>|<at>[^<]*</at>")


def _context(request: Request) -> ChatContext:
    state = request.app.state
    return ChatContext(state.database, state.settings, state.agent_models, state.tool_registry,
                       state.rate_limiter)  # fmt: skip


def _spawn(request: Request, work: Coroutine[Any, Any, None]) -> None:
    """Run after responding; tasks are kept on app.state so they are not garbage-collected."""
    tasks: set[asyncio.Task] = request.app.state.__dict__.setdefault("chat_tasks", set())
    task = asyncio.create_task(work)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _seen(request: Request, event_id: str) -> bool:
    """Slack retries deliveries; process each event id once."""
    recent: deque[str] = request.app.state.__dict__.setdefault("chat_seen", deque(maxlen=1000))
    if event_id in recent:
        return True
    recent.append(event_id)
    return False


def _connector(request: Request, name: str) -> Any:
    service = request.app.state.integrations.connectors.get(name)
    if service is None:
        raise NotFoundError(f"{name} is not configured")
    return service


async def _verified_slack_body(request: Request) -> bytes:
    body = await request.body()
    secret = request.app.state.settings.slack_signing_secret.get_secret_value()
    if not verify_slack_signature(secret, request.headers.get("x-slack-request-timestamp", ""),
                                  request.headers.get("x-slack-signature", ""), body):  # fmt: skip
        raise AuthenticationError("Invalid Slack signature")
    return body


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    slack = _connector(request, "slack")
    payload = json.loads(await _verified_slack_body(request))
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})
    event = payload.get("event") or {}
    is_question = event.get("type") == "app_mention" or (
        event.get("type") == "message" and event.get("channel_type") == "im"
    )
    if (payload.get("type") != "event_callback" or not is_question or event.get("bot_id")
            or event.get("subtype") or _seen(request, payload.get("event_id", ""))):  # fmt: skip
        return Response(status_code=200)

    context = _context(request)

    async def work() -> None:
        try:
            email = await slack.email_of(event.get("user", ""))
            answer = await answer_as(context, email, _MENTION.sub("", event.get("text", "")))
            await slack.post_message(event.get("channel"), answer.text,
                                     thread_ts=event.get("thread_ts") or event.get("ts"),
                                     blocks=approval_blocks(answer.text, answer.pending_approvals)
                                     if answer.pending_approvals else None)  # fmt: skip
        except Exception:
            logger.exception("Slack event handling failed")

    _spawn(request, work())
    return Response(status_code=200)


@router.post("/slack/interactions")
async def slack_interactions(request: Request) -> Response:
    slack = _connector(request, "slack")
    body = await _verified_slack_body(request)
    payload = json.loads(parse_qs(body.decode()).get("payload", ["{}"])[0])
    actions = [
        a for a in payload.get("actions") or [] if a.get("action_id") in {"approve", "reject"}
    ]
    if payload.get("type") != "block_actions" or not actions:
        return Response(status_code=200)
    action, context = actions[0], _context(request)
    channel = (payload.get("channel") or {}).get("id")
    thread = (payload.get("message") or {}).get("thread_ts") or (payload.get("message") or {}).get(
        "ts"
    )

    async def work() -> None:
        try:
            email = await slack.email_of((payload.get("user") or {}).get("id", ""))
            result = await decide_as(
                context, email, action.get("value", ""), action["action_id"] == "approve"
            )
            await slack.post_message(channel, result, thread_ts=thread)
        except Exception:
            logger.exception("Slack interaction handling failed")

    _spawn(request, work())
    return Response(status_code=200)


@router.post("/teams/messages")
async def teams_messages(request: Request) -> Response:
    teams = _connector(request, "teams_chat")
    body = await request.body()
    secret = request.app.state.settings.teams_outgoing_webhook_secret.get_secret_value()
    if not verify_teams_hmac(secret, request.headers.get("authorization", ""), body):
        raise AuthenticationError("Invalid Teams signature")
    activity = json.loads(body)
    question = _MENTION.sub("", activity.get("text", "")).strip()
    sender = activity.get("from") or {}
    context = _context(request)

    async def work() -> None:
        try:
            email = await teams.email_of(sender.get("aadObjectId", ""))
            answer = await answer_as(context, email, question)
            await teams.post_message(
                None, f"**{sender.get('name', 'Someone')}** asked: {question}\n\n{answer.text}"
            )
        except Exception:
            logger.exception("Teams message handling failed")

    _spawn(request, work())
    return JSONResponse(
        {"type": "message", "text": "Working on it, I'll post the answer here shortly."}
    )
