"""Slack and Microsoft Teams clients (outbound side of the chat front-ends).

Inbound requests (Slack Events API, Teams outgoing webhooks) are handled in
``api/v1/chat.py`` with signature verification; these services post answers and look up
who a chat user is.
"""

import base64
import hashlib
import hmac
import re
from typing import Any

from backend.app.core.config import Settings
from backend.app.integrations.connectors.auth import microsoft_token
from backend.app.integrations.connectors.base import ConnectorClient, ConnectorService, safe_id
from backend.app.integrations.connectors.spec import ConnectorSpec

CHANNEL = re.compile(r"^[A-Z0-9]{8,12}$|^#[a-z0-9._-]{1,80}$")


class SlackService(ConnectorService):
    source = "slack"

    def __init__(self, client_factory: Any, default_channel: str = "") -> None:
        super().__init__(client_factory)
        self.default_channel = default_channel

    async def _call(self, method: str, payload: dict[str, Any] | None = None,
                    params: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
        body = (await self.client.post(f"/{method}", payload) if payload is not None
                else await self.client.get(f"/{method}", params))  # fmt: skip
        if not body.get("ok"):
            raise ValueError(f"Slack {method}: {body.get('error', 'failed')}")
        return body

    async def email_of(self, user_id: str) -> str | None:
        body = await self._call("users.info", params={"user": safe_id(user_id, "Slack user")})
        return ((body.get("user") or {}).get("profile") or {}).get("email")

    async def post_message(self, channel: str | None, text: str, *, thread_ts: str | None = None,
                           blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
        channel = channel or self.default_channel
        if not channel or not CHANNEL.fullmatch(channel):
            raise ValueError("A Slack channel id (C0123…) or #name is required")
        payload: dict[str, Any] = {"channel": channel, "text": text[:3900], "unfurl_links": False}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if blocks:
            payload["blocks"] = blocks
        body = await self._call("chat.postMessage", payload)
        return {"channel": body.get("channel"), "ts": body.get("ts")}

    async def health_check(self) -> dict[str, Any]:
        body = await self._call("auth.test", payload={})
        return {"team": body.get("team"), "bot": body.get("user")}


def verify_teams_hmac(secret_b64: str, authorization: str, body: bytes) -> bool:
    """Teams outgoing webhook: ``Authorization: HMAC <base64(HMAC-SHA256(key, body))>``."""
    if not (secret_b64 and authorization.startswith("HMAC ")):
        return False
    try:
        key = base64.b64decode(secret_b64)
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, authorization.removeprefix("HMAC ").strip())


class TeamsChatService(ConnectorService):
    """Posts answers to a Teams Workflows webhook; resolves people via Graph when configured."""

    source = "teams_chat"

    def __init__(self, client_factory: Any, webhook_url: str, graph_factory: Any = None) -> None:
        super().__init__(client_factory)
        self.webhook_url = webhook_url
        self._graph_factory = graph_factory
        self._graph: Any = None

    async def post_message(self, channel: str | None, text: str, **_: Any) -> dict[str, Any]:
        card = {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "body": [{"type": "TextBlock", "text": text[:3900], "wrap": True}]},
        }]}  # fmt: skip
        response = await self.client._client.post(self.webhook_url, json=card)
        response.raise_for_status()
        return {"status": response.status_code}

    async def email_of(self, aad_object_id: str) -> str | None:
        if self._graph_factory is None:
            return None
        if self._graph is None:
            self._graph = self._graph_factory()
        user = await self._graph.get(f"/users/{safe_id(aad_object_id, 'AAD object id')}",
                                     {"$select": "mail,userPrincipalName"})  # fmt: skip
        return user.get("mail") or user.get("userPrincipalName")

    async def close(self) -> None:
        await super().close()
        if self._graph is not None:
            await self._graph.close()

    async def health_check(self) -> dict[str, Any]:
        return {"webhook": bool(self.webhook_url)}


class DemoChat:
    def __init__(self, source: str) -> None:
        self.source = source
        self.sent: list[dict[str, Any]] = []

    async def post_message(self, channel: str | None, text: str, **kwargs: Any) -> dict[str, Any]:
        self.sent.append({"channel": channel, "text": text, **kwargs})
        return {"channel": channel, "ts": str(len(self.sent)), "mode": "demo"}

    async def email_of(self, user_id: str) -> str | None:
        return None

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _teams(s: Settings) -> TeamsChatService:
    graph = None
    if s.ms_tenant_id and s.ms_client_id and s.ms_client_secret.get_secret_value():
        token = microsoft_token(s)

        def graph() -> ConnectorClient:
            return ConnectorClient("Microsoft Graph", base_url="https://graph.microsoft.com/v1.0",
                                   timeout=s.connector_timeout_seconds, token_provider=token)  # fmt: skip

    return TeamsChatService(
        lambda: ConnectorClient(
            "Teams", base_url="https://graph.microsoft.com", timeout=s.connector_timeout_seconds
        ),
        s.teams_workflow_webhook_url.get_secret_value(),
        graph,
    )


CHAT_SPECS = (
    ConnectorSpec(
        "slack", "Slack", "chat", required=("slack_bot_token", "slack_signing_secret"),
        optional=("slack_digest_channel",),
        setup="api.slack.com/apps → bot scopes app_mentions:read, chat:write, im:history, users:read, "
              "users:read.email; Event Subscriptions → https://<host>/api/v1/chat/slack/events "
              "(app_mention, message.im); Interactivity → https://<host>/api/v1/chat/slack/interactions",
        docs_url="https://api.slack.com/apis/events-api",
        build=lambda s: SlackService(
            lambda: ConnectorClient("Slack", base_url="https://slack.com/api", timeout=s.connector_timeout_seconds,
                                    headers={"Authorization": f"Bearer {s.slack_bot_token.get_secret_value()}"}),
            s.slack_digest_channel),
        demo=lambda data, today: DemoChat("slack"),
    ),
    ConnectorSpec(
        "teams_chat", "Microsoft Teams chat", "chat",
        required=("teams_outgoing_webhook_secret", "teams_workflow_webhook_url"),
        setup="Teams → channel → Manage → Outgoing webhook to https://<host>/api/v1/chat/teams/messages "
              "(copy the security token); a Workflows 'post to channel when a webhook request is "
              "received' URL for answers; MS_* settings map senders to platform users",
        docs_url="https://learn.microsoft.com/microsoftteams/platform/webhooks-and-connectors/how-to/add-outgoing-webhook",
        build=_teams,
        demo=lambda data, today: DemoChat("teams_chat"),
    ),
)  # fmt: skip
