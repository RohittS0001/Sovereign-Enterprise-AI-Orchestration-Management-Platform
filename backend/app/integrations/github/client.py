import httpx

from backend.app.core.config.settings import settings
from backend.app.core.exceptions.exceptions import ConfigurationError
from backend.app.integrations.common.http import HttpIntegrationClient


class GitHubClient(HttpIntegrationClient):
    """GitHub REST API (github.com or GitHub Enterprise Server)."""

    display_name = "GitHub"
    integration = "github"

    def __init__(
        self,
        token: str | None = None,
        api_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        token = token or settings.github_token.get_secret_value()
        if not token:
            raise ConfigurationError("GITHUB_TOKEN is not configured")

        super().__init__(
            base_url=api_url or settings.github_api_url,
            timeout=timeout or settings.github_timeout_seconds,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
        )

    async def health_check(self) -> dict:
        # /rate_limit does not count against the rate limit.
        return await self.get("/rate_limit")
