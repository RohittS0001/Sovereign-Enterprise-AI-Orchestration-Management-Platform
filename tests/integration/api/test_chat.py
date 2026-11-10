"""Slack and Teams front-ends: signed requests, agent runs as the mapped user, approvals."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.core.security.permissions import Role
from backend.app.db.models import ApprovalRequest, ApprovalStatus
from backend.app.domains.chat.service import verify_slack_signature
from backend.app.integrations.connectors.chat import verify_teams_hmac
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, models, plan

SIGNING = "slack-signing-secret"  # noqa: S105
TEAMS_KEY = base64.b64encode(b"teams-shared-secret").decode()


class FakeSlack:
    def __init__(self, emails: dict[str, str]) -> None:
        self.emails = emails
        self.posts: list[dict] = []

    async def email_of(self, user_id: str) -> str | None:
        return self.emails.get(user_id)

    async def post_message(self, channel, text, **kwargs):
        self.posts.append({"channel": channel, "text": text, **kwargs})
        return {"ok": True}


def slack_headers(body: bytes, *, ts: int | None = None, secret: str = SIGNING) -> dict[str, str]:
    ts = ts or int(time.time())
    signature = (
        "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    )
    return {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": signature,
            "Content-Type": "application/json"}  # fmt: skip


def mention(text: str, user: str = "U1", event_id: str = "Ev1") -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "event_id": event_id,
            "event": {
                "type": "app_mention",
                "user": user,
                "text": f"<@UBOT> {text}",
                "channel": "C123",
                "ts": "1.0",
            },
        }
    ).encode()


async def drain(app) -> None:
    await asyncio.gather(*list(getattr(app.state, "chat_tasks", set())))


def test_signature_helpers() -> None:
    body = b'{"x":1}'
    headers = slack_headers(body, ts=1_000)
    assert verify_slack_signature(SIGNING, "1000", headers["X-Slack-Signature"], body, now=1_100)
    assert not verify_slack_signature(
        SIGNING, "1000", headers["X-Slack-Signature"], body, now=2_000
    )  # stale
    assert not verify_slack_signature(
        SIGNING, "1000", headers["X-Slack-Signature"], b'{"x":2}', now=1_100
    )
    good = (
        "HMAC "
        + base64.b64encode(hmac.new(b"teams-shared-secret", body, hashlib.sha256).digest()).decode()
    )
    assert verify_teams_hmac(TEAMS_KEY, good, body)
    assert not verify_teams_hmac(TEAMS_KEY, good, b"tampered") and not verify_teams_hmac(
        TEAMS_KEY, "Bearer x", body
    )


@pytest.fixture
def chat_settings(test_settings):
    return test_settings.model_copy(update={
        "demo_mode": True, "slack_signing_secret": SecretStr(SIGNING),
        "teams_outgoing_webhook_secret": SecretStr(TEAMS_KEY),
        "approval_allow_self_approval": True})  # fmt: skip


async def test_slack_rejects_bad_signatures_and_answers_url_verification(
    chat_settings, database
) -> None:
    app = create_app(chat_settings, database)
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    async with serve(app) as client:
        bad = await client.post(
            "/api/v1/chat/slack/events", content=body, headers=slack_headers(body, secret="nope")
        )
        ok = await client.post(
            "/api/v1/chat/slack/events", content=body, headers=slack_headers(body)
        )

    assert bad.status_code == 401 and ok.json() == {"challenge": "abc"}


async def test_mention_runs_the_agent_as_the_mapped_user_with_approval_buttons(
    chat_settings, database, make_user
) -> None:
    lead = await make_user(role=Role.TEAM_LEAD)
    agent_models, _, _ = models([plan(("jira.add_comment", {"issue_key": "PHX-1", "comment": "From Slack"})), DONE],
                                ["Drafted the comment; it needs approval."])  # fmt: skip
    app = create_app(chat_settings, database, agent_models=agent_models)
    slack = FakeSlack({"U1": lead.email})
    app.state.integrations.connectors["slack"] = slack

    async with serve(app) as client:
        body = mention("comment on PHX-1 that we are on it")
        response = await client.post(
            "/api/v1/chat/slack/events", content=body, headers=slack_headers(body)
        )
        await drain(app)
        retry = await client.post(
            "/api/v1/chat/slack/events", content=body, headers=slack_headers(body)
        )
        await drain(app)

        [post] = slack.posts  # the retried delivery was ignored
        assert response.status_code == 200 and retry.status_code == 200
        assert post["thread_ts"] == "1.0" and "needs approval" in post["text"]
        approval_id = post["blocks"][1]["elements"][0]["value"]

        click = urlencode({"payload": json.dumps({
            "type": "block_actions", "user": {"id": "U1"}, "channel": {"id": "C123"},
            "message": {"ts": "2.0", "thread_ts": "1.0"},
            "actions": [{"action_id": "approve", "value": approval_id}]})}).encode()  # fmt: skip
        headers = {**slack_headers(click), "Content-Type": "application/x-www-form-urlencoded"}
        assert (
            await client.post("/api/v1/chat/slack/interactions", content=click, headers=headers)
        ).status_code == 200
        await drain(app)

    assert slack.posts[-1]["text"] == "jira.add_comment: executed"
    async with database.session() as session:
        approval = await session.scalar(select(ApprovalRequest))
    assert approval.status == ApprovalStatus.EXECUTED and approval.decided_by_id == lead.id


async def test_unknown_users_and_bot_messages(chat_settings, database) -> None:
    agent_models, _, _ = models([DONE], ["unused"])
    app = create_app(chat_settings, database, agent_models=agent_models)
    slack = FakeSlack({"U9": "stranger@example.com"})
    app.state.integrations.connectors["slack"] = slack
    bot = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev7",
            "event": {
                "type": "message",
                "channel_type": "im",
                "bot_id": "B1",
                "text": "echo",
                "channel": "D1",
            },
        }
    ).encode()

    async with serve(app) as client:
        stranger = mention("hi", user="U9", event_id="Ev8")
        await client.post(
            "/api/v1/chat/slack/events", content=stranger, headers=slack_headers(stranger)
        )
        await client.post("/api/v1/chat/slack/events", content=bot, headers=slack_headers(bot))
        await drain(app)

    [post] = slack.posts
    assert "don't recognise you" in post["text"]


async def test_teams_outgoing_webhook(chat_settings, database, make_user) -> None:
    pm = await make_user(role=Role.PROJECT_MANAGER)
    agent_models, _, _ = models(
        [plan(("analytics.list_projects", {})), DONE], ["No projects yet [S1]."]
    )
    app = create_app(chat_settings, database, agent_models=agent_models)

    class FakeTeams:
        def __init__(self) -> None:
            self.posts: list[str] = []

        async def email_of(self, aad_id: str) -> str | None:
            return pm.email if aad_id == "aad-1" else None

        async def post_message(self, channel, text, **kwargs):
            self.posts.append(text)

    teams = FakeTeams()
    app.state.integrations.connectors["teams_chat"] = teams
    body = json.dumps({"type": "message", "text": "<at>Sovereign</at> which projects exist?",
                       "from": {"aadObjectId": "aad-1", "name": "Priya"}}).encode()  # fmt: skip
    signature = (
        "HMAC "
        + base64.b64encode(hmac.new(b"teams-shared-secret", body, hashlib.sha256).digest()).decode()
    )

    async with serve(app) as client:
        forged = await client.post(
            "/api/v1/chat/teams/messages", content=body, headers={"Authorization": "HMAC x"}
        )
        ack = await client.post(
            "/api/v1/chat/teams/messages", content=body, headers={"Authorization": signature}
        )
        await drain(app)

    assert forged.status_code == 401
    assert ack.json()["type"] == "message"
    assert (
        teams.posts[0].startswith("**Priya** asked: which projects exist?")
        and "No projects yet" in teams.posts[0]
    )
