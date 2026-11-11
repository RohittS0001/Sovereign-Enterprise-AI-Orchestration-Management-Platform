"""Customer support (Zendesk): open tickets tagged with a project, i.e. customer impact."""

import re
from typing import Any

from backend.app.core.config import Settings
from backend.app.integrations.connectors.base import (
    ConnectorClient,
    ConnectorService,
    SupportTicket,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec

_TAG = re.compile(r"^[a-z0-9_./-]{1,80}$")


def zendesk_tag(value: str) -> str:
    """Tags go into Zendesk's search syntax: lowercase, no spaces or operators."""
    value = value.lower()
    if not _TAG.fullmatch(value):
        raise ValueError(f"Invalid Zendesk tag: {value!r}")
    return value


class ZendeskService(ConnectorService):
    source = "zendesk"

    def __init__(self, client_factory: Any, subdomain: str) -> None:
        super().__init__(client_factory)
        self.subdomain = subdomain

    async def list_tickets(
        self, tags: list[str], open_only: bool = True, limit: int = 50
    ) -> list[SupportTicket]:
        query = "type:ticket " + " ".join(f"tags:{zendesk_tag(t)}" for t in tags)
        if open_only:
            query += " status<solved"
        body = await self.client.get(
            "/api/v2/search.json",
            {"query": query, "sort_by": "updated_at", "per_page": min(limit, 100)},
        )
        return [
            SupportTicket(
                source=self.source,
                id=str(t.get("id")),
                subject=t.get("subject") or "",
                status=t.get("status") or "",
                priority=t.get("priority"),
                created=to_date(t.get("created_at")),
                updated=to_date(t.get("updated_at")),
                tags=list(t.get("tags") or []),
                url=f"https://{self.subdomain}.zendesk.com/agent/tickets/{t.get('id')}",
            )
            for t in body.get("results") or []
        ][:limit]

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/api/v2/users/me.json")
        return {"user": (body.get("user") or {}).get("email")}


class DemoSupport:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.data = data

    async def list_tickets(
        self, tags: list[str], open_only: bool = True, limit: int = 50
    ) -> list[SupportTicket]:
        wanted = {t.lower() for t in tags}
        rows = [SupportTicket(source=self.source, **r) for r in self.data.get("tickets", [])]
        return [
            r
            for r in rows
            if wanted & {t.lower() for t in r.tags}
            and (not open_only or r.status not in {"solved", "closed"})
        ][:limit]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _zendesk(s: Settings) -> ZendeskService:
    return ZendeskService(
        lambda: ConnectorClient(
            "Zendesk",
            base_url=f"https://{s.zendesk_subdomain}.zendesk.com",
            timeout=s.connector_timeout_seconds,
            auth=(f"{s.zendesk_email}/token", s.zendesk_api_token.get_secret_value()),
        ),
        s.zendesk_subdomain,
    )


def _demo(data: dict[str, Any], today: Any) -> DemoSupport:
    return DemoSupport("zendesk", (data.get("support") or {}).get("zendesk") or {})


SUPPORT_SPECS = (
    ConnectorSpec(
        "zendesk",
        "Zendesk",
        "support",
        required=("zendesk_subdomain", "zendesk_email", "zendesk_api_token"),
        setup="Zendesk Admin Center → Apps and integrations → Zendesk API → enable token access, add a "
        "token; tag tickets with the project key (lowercase)",
        docs_url="https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/",
        build=_zendesk,
        demo=_demo,
    ),
)
