"""Token providers shared by connectors: OAuth client credentials, refresh tokens, Google.

Each provider caches its access token until shortly before expiry and is used from a
client's ``_auth_headers`` / ``_on_unauthorized`` hooks (see ``base.py``).
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from backend.app.core.errors import IntegrationError

EXPIRY_MARGIN_SECONDS = 60


class OAuthToken:
    """OAuth 2.0 token endpoint: client credentials (default) or a refresh-token grant.

    ``auth_style="basic"`` sends client id/secret as HTTP Basic (Zoom), otherwise in the
    form body (Microsoft, Salesforce, Zoho).
    """

    def __init__(
        self,
        name: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        scope: str | None = None,
        refresh_token: str | None = None,
        extra: dict[str, str] | None = None,
        auth_style: str = "body",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.refresh_token = refresh_token
        self.extra = extra or {}
        self.auth_style = auth_style
        self.timeout = timeout
        self.transport = transport
        self.clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.response: dict[str, Any] = {}  # last token response (e.g. Salesforce instance_url)

    def invalidate(self) -> None:
        self._token, self._expires_at = None, 0.0

    async def get(self) -> str:
        async with self._lock:
            if self._token and self.clock() < self._expires_at:
                return self._token
            form = {
                "grant_type": "refresh_token" if self.refresh_token else "client_credentials",
                **self.extra,
            }
            if self.refresh_token:
                form["refresh_token"] = self.refresh_token
            if self.scope:
                form["scope"] = self.scope
            auth = None
            if self.auth_style == "basic":
                auth = (self.client_id, self.client_secret)
            else:
                form |= {"client_id": self.client_id, "client_secret": self.client_secret}
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, transport=self.transport
                ) as client:
                    response = await client.post(self.token_url, data=form, auth=auth)
            except httpx.HTTPError as exc:
                raise IntegrationError(
                    f"Could not reach the {self.name} token endpoint", integration=self.name
                ) from exc
            if response.is_error:
                raise IntegrationError(
                    f"{self.name} token request failed ({response.status_code})",
                    integration=self.name,
                    upstream_status=response.status_code,
                )
            body = response.json()
            token = body.get("access_token")
            if not token:
                raise IntegrationError(
                    f"{self.name} token response had no access_token", integration=self.name
                )
            self.response = body
            self._token = token
            self._expires_at = (
                self.clock() + float(body.get("expires_in") or 3600) - EXPIRY_MARGIN_SECONDS
            )
            return token


def microsoft_token(settings: Any, **kwargs: Any) -> OAuthToken:
    """Microsoft Graph application token (client credentials) from the MS_* settings."""
    return OAuthToken(
        "Microsoft Graph",
        f"https://login.microsoftonline.com/{settings.ms_tenant_id}/oauth2/v2.0/token",
        settings.ms_client_id,
        settings.ms_client_secret.get_secret_value(),
        scope="https://graph.microsoft.com/.default",
        timeout=settings.connector_timeout_seconds,
        **kwargs,
    )


def zoho_token(settings: Any, **kwargs: Any) -> OAuthToken:
    return OAuthToken(
        "Zoho",
        f"{settings.zoho_accounts_url.rstrip('/')}/oauth/v2/token",
        settings.zoho_client_id,
        settings.zoho_client_secret.get_secret_value(),
        refresh_token=settings.zoho_refresh_token.get_secret_value(),
        timeout=settings.connector_timeout_seconds,
        **kwargs,
    )


class GoogleServiceAccountToken:
    """Access tokens for a Google service account (optionally impersonating a user)."""

    def __init__(self, key_file: Path, scopes: list[str], subject: str | None = None) -> None:
        self.key_file = key_file
        self.scopes = scopes
        self.subject = subject or None
        self._credentials: Any = None
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        if self._credentials is not None:
            self._credentials.token = None

    async def get(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        async with self._lock:
            if self._credentials is None:
                self._credentials = service_account.Credentials.from_service_account_file(
                    str(self.key_file), scopes=self.scopes, subject=self.subject
                )
            if not self._credentials.valid:
                try:
                    await asyncio.to_thread(self._credentials.refresh, Request())
                except Exception as exc:
                    raise IntegrationError(
                        "Google service-account token request failed", integration="google"
                    ) from exc
            return self._credentials.token


# ------------------------------------------------------------------ AWS Signature Version 4


def aws_sigv4_headers(
    method: str,
    url: str,
    *,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    session_token: str = "",
    now: Any = None,
) -> dict[str, str]:
    """Headers that sign an AWS API request (SigV4), so no AWS SDK is needed.

    https://docs.aws.amazon.com/IAM/latest/UserGuide/create-signed-request.html
    """
    import hashlib
    import hmac
    from datetime import UTC, datetime
    from urllib.parse import quote, urlsplit

    moment = now or datetime.now(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    day = moment.strftime("%Y%m%d")
    parts = urlsplit(url)
    signed = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    signed["host"] = parts.netloc
    signed["x-amz-date"] = amz_date
    if session_token:
        signed["x-amz-security-token"] = session_token
    query = "&".join(
        sorted(
            f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
            for k, _, v in (pair.partition("=") for pair in parts.query.split("&") if pair)
        )
    )
    names = sorted(signed)
    canonical = "\n".join(
        [
            method.upper(),
            quote(parts.path or "/", safe="/-_.~"),
            query,
            "".join(f"{n}:{signed[n]}\n" for n in names),
            ";".join(names),
            hashlib.sha256(body).hexdigest(),
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )

    def mac(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    key = mac(mac(mac(mac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    result = {k: v for k, v in signed.items() if k != "host"}
    result["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return result


def azure_management_token(settings: Any, **kwargs: Any) -> "OAuthToken":
    """Azure Resource Manager token (client credentials) from the AZURE_* settings."""
    return OAuthToken(
        "Azure",
        f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/token",
        settings.azure_client_id,
        settings.azure_client_secret.get_secret_value(),
        scope="https://management.azure.com/.default",
        timeout=settings.connector_timeout_seconds,
        **kwargs,
    )
