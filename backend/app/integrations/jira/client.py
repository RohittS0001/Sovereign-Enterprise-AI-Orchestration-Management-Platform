from typing import Any

import httpx

from backend.app.core.config.settings import settings


class JiraClient:
    """Low-level async client for Jira REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url or settings.jira_base_url
        ).rstrip("/")

        self.email = email or settings.jira_email
        self.api_token = api_token or settings.jira_api_token
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("JIRA_BASE_URL is not configured")

        if not self.email:
            raise ValueError("JIRA_EMAIL is not configured")

        if not self.api_token:
            raise ValueError("JIRA_API_TOKEN is not configured")

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.email, self.api_token),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.get(
            path,
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.post(
            path,
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def put(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.put(
            path,
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def delete(self, path: str) -> None:
        response = await self._client.delete(path)
        response.raise_for_status()

    async def get_current_user(self) -> dict[str, Any]:
        """Return the authenticated Jira user."""
        return await self.get("/rest/api/3/myself")
    
    async def close(self) -> None:
        await self._client.aclose()