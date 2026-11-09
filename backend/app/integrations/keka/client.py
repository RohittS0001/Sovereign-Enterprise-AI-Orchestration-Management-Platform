"""Keka HRMS API client.

Auth: POST form ``grant_type=kekaapi&scope=kekaapi&client_id&client_secret&api_key`` to
``https://login.keka.com/connect/token`` → bearer token (typically valid 24h), cached
and refreshed shortly before expiry or after a 401.
"""

import asyncio
import time

import httpx

from backend.app.core.config.settings import settings
from backend.app.core.exceptions.exceptions import ConfigurationError, IntegrationError
from backend.app.integrations.common.http import HttpIntegrationClient, error_message

_REFRESH_MARGIN_SECONDS = 120


class KekaClient(HttpIntegrationClient):
    display_name = "Keka"
    integration = "keka"

    def __init__(
        self,
        base_url: str | None = None,
        token_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = base_url or settings.keka_base_url
        self.token_url = token_url or settings.keka_token_url
        self.client_id = client_id or settings.keka_client_id
        self._client_secret = client_secret or settings.keka_client_secret.get_secret_value()
        self._api_key = api_key or settings.keka_api_key.get_secret_value()

        missing = [
            name
            for name, value in (
                ("KEKA_BASE_URL", base_url),
                ("KEKA_CLIENT_ID", self.client_id),
                ("KEKA_CLIENT_SECRET", self._client_secret),
                ("KEKA_API_KEY", self._api_key),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(f"Keka is not configured: set {', '.join(missing)}")

        super().__init__(
            base_url=base_url,
            timeout=timeout or settings.keka_timeout_seconds,
            # Keka's docs recommend a browser-like user agent for API calls.
            headers={"User-Agent": "Mozilla/5.0 (compatible; SovereignPlatform/1.0)"},
            transport=transport,
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}"}

    async def _on_unauthorized(self) -> bool:
        self._token = None
        return True

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            try:
                response = await self._client.post(
                    self.token_url,
                    data={
                        "grant_type": "kekaapi",
                        "scope": "kekaapi",
                        "client_id": self.client_id,
                        "client_secret": self._client_secret,
                        "api_key": self._api_key,
                    },
                )
            except httpx.RequestError as exc:
                raise IntegrationError(
                    f"Could not reach Keka login: {exc.__class__.__name__}", integration="keka"
                ) from exc

            if response.is_error:
                raise IntegrationError(
                    f"Keka token request failed ({response.status_code}): "
                    f"{error_message(response)}",
                    integration="keka",
                    upstream_status=response.status_code,
                )

            body = response.json()
            self._token = body["access_token"]
            lifetime = float(body.get("expires_in") or 3600)
            self._token_expires_at = time.monotonic() + max(
                60.0, lifetime - _REFRESH_MARGIN_SECONDS
            )
            return self._token

    async def health_check(self) -> dict:
        return await self.get("/hris/employees", params={"pageSize": 1})
