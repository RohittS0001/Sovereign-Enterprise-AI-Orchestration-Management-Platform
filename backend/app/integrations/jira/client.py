from types import TracebackType
from typing import Any, Self

import httpx

from backend.app.core.config.settings import settings
from backend.app.core.exceptions.exceptions import ConfigurationError, IntegrationError
from backend.app.core.logging.logger import get_logger

logger = get_logger(__name__)


class JiraClient:
    """Low-level async client for the Jira REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.jira_base_url).rstrip("/")
        self.email = email or settings.jira_email
        self.api_token = api_token or settings.jira_api_token.get_secret_value()
        self.timeout = timeout or settings.jira_timeout_seconds

        if not self.base_url:
            raise ConfigurationError("JIRA_BASE_URL is not configured")
        if not self.email:
            raise ConfigurationError("JIRA_EMAIL is not configured")
        if not self.api_token:
            raise ConfigurationError("JIRA_API_TOKEN is not configured")

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.email, self.api_token),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, params=params, json=payload)
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                f"Jira request timed out: {method} {path}", integration="jira"
            ) from exc
        except httpx.RequestError as exc:
            raise IntegrationError(
                f"Could not reach Jira: {exc.__class__.__name__}", integration="jira"
            ) from exc

        if response.is_error:
            message = _error_message(response)
            logger.warning(
                "Jira request failed",
                extra={"method": method, "path": path, "upstream_status": response.status_code},
            )
            raise IntegrationError(
                f"Jira returned {response.status_code} for {method} {path}: {message}",
                integration="jira",
                upstream_status=response.status_code,
            )

        # Several Jira write endpoints answer 201/204 with an empty body.
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request("POST", path, payload=payload)

    async def put(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request("PUT", path, payload=payload)

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def get_current_user(self) -> dict[str, Any]:
        """Return the authenticated Jira user."""
        return await self.get("/rest/api/3/myself")

    async def close(self) -> None:
        await self._client.aclose()


def _error_message(response: httpx.Response) -> str:
    """Extract Jira's error text without echoing large or unexpected bodies."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase

    if isinstance(body, dict):
        messages = list(body.get("errorMessages") or [])
        errors = body.get("errors") or {}
        if isinstance(errors, dict):
            messages.extend(f"{field}: {text}" for field, text in errors.items())
        if messages:
            return "; ".join(str(m) for m in messages)[:500]

    return response.reason_phrase
