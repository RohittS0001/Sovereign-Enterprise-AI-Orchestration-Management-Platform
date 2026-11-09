from typing import Any

import httpx

from backend.app.core.config import settings
from backend.app.core.errors import ConfigurationError
from backend.app.integrations.common.http import HttpIntegrationClient


class JiraClient(HttpIntegrationClient):
    """Low-level async client for the Jira REST API."""

    display_name = "Jira"
    integration = "jira"

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = base_url or settings.jira_base_url
        email = email or settings.jira_email
        api_token = api_token or settings.jira_api_token.get_secret_value()

        if not base_url:
            raise ConfigurationError("JIRA_BASE_URL is not configured")
        if not email:
            raise ConfigurationError("JIRA_EMAIL is not configured")
        if not api_token:
            raise ConfigurationError("JIRA_API_TOKEN is not configured")

        self.email = email
        self.api_token = api_token
        super().__init__(
            base_url=base_url,
            timeout=timeout or settings.jira_timeout_seconds,
            headers={"Content-Type": "application/json"},
            auth=(email, api_token),
            transport=transport,
        )

    async def get_current_user(self) -> dict[str, Any]:
        """Return the authenticated Jira user (also used as the health check)."""
        return await self.get("/rest/api/3/myself")
