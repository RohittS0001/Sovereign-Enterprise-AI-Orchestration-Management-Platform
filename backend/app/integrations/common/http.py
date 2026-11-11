"""Shared async HTTP client for integrations.

Gives every integration the same behaviour: errors mapped to ``IntegrationError``
(with the upstream status, so tools can mark them retryable), empty 201/204 bodies
handled, and polite waiting on rate limits (``Retry-After`` / GitHub's
``x-ratelimit-reset``) for idempotent GET requests. Response bodies are streamed and
reading stops at ``max_response_bytes``, so an oversized (or decompression-bomb) response
never ends up in memory.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self

import httpx

from backend.app.core.errors import IntegrationError
from backend.app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_RESPONSE_BYTES = 25_000_000
_HOP_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


async def read_limited(response: httpx.Response, max_bytes: int, *, integration: str) -> bytes:
    """Read a streamed response body, stopping as soon as it exceeds ``max_bytes``.

    The limit applies to the decoded bytes, so a small gzip body that expands past it is
    stopped too. A declared Content-Length above the limit is refused before reading.
    """
    declared = response.headers.get("content-length", "")
    too_large = IntegrationError(
        f"Response from {integration} is larger than {max_bytes} bytes",
        integration=integration,
        upstream_status=response.status_code,
    )
    if declared.isdigit() and int(declared) > max_bytes:
        raise too_large
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk)
        if len(data) > max_bytes:
            raise too_large
    return bytes(data)


def buffered(response: httpx.Response, body: bytes) -> httpx.Response:
    """A plain, already-read Response holding ``body`` (decoded, so encoding headers go)."""
    headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() not in _HOP_HEADERS]
    return httpx.Response(
        response.status_code, headers=headers, content=body, request=response.request
    )


def error_message(response: httpx.Response) -> str:
    """Extract an upstream error text (Jira, GitHub and Keka formats) safely."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase

    messages: list[str] = []
    if isinstance(body, dict):
        messages.extend(str(m) for m in body.get("errorMessages") or [])  # Jira
        if body.get("message"):  # GitHub, Keka
            messages.append(str(body["message"]))
        errors = body.get("errors")
        if isinstance(errors, dict):  # Jira field errors
            messages.extend(f"{field}: {text}" for field, text in errors.items())
        elif isinstance(errors, list):  # GitHub / Keka
            for item in errors:
                messages.append(
                    str(item.get("message", item)) if isinstance(item, dict) else str(item)
                )
        if body.get("error") and not messages:  # OAuth token endpoints
            messages.append(str(body.get("error_description") or body["error"]))

    return "; ".join(messages)[:500] if messages else response.reason_phrase


class HttpIntegrationClient:
    """Base class; subclasses set ``display_name`` and optionally override hooks."""

    display_name = "Integration"
    integration = "integration"

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | tuple[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_rate_limit_waits: int = 2,
        max_wait_seconds: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_response_bytes = max_response_bytes
        self.max_rate_limit_waits = max_rate_limit_waits
        self.max_wait_seconds = max_wait_seconds
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json", **(headers or {})},
            auth=auth,
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

    # ------------------------------------------------------------------ hooks

    async def _auth_headers(self) -> dict[str, str]:
        """Per-request auth headers (e.g. a cached OAuth token)."""
        return {}

    async def _on_unauthorized(self) -> bool:
        """Called on 401; return True to retry once (e.g. after refreshing a token)."""
        return False

    # ------------------------------------------------------------------ core

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        rate_limit_waits = 0
        reauthenticated = False

        while True:
            response = await self._send(method, path, params=params, payload=payload)

            if (
                response.status_code == 401
                and not reauthenticated
                and await self._on_unauthorized()
            ):
                reauthenticated = True
                continue

            if self._is_rate_limited(response):
                wait = self._rate_limit_wait(response)
                if (
                    method == "GET"
                    and rate_limit_waits < self.max_rate_limit_waits
                    and wait is not None
                    and wait <= self.max_wait_seconds
                ):
                    rate_limit_waits += 1
                    logger.info(
                        "Rate limited; waiting",
                        extra={"integration": self.integration, "wait_s": wait},
                    )
                    await self._sleep(wait)
                    continue
                raise IntegrationError(
                    f"{self.display_name} rate limit reached for {method} {path}",
                    integration=self.integration,
                    upstream_status=429,
                )

            if response.is_error:
                logger.warning(
                    "Integration request failed",
                    extra={
                        "integration": self.integration,
                        "method": method,
                        "path": path,
                        "upstream_status": response.status_code,
                    },
                )
                raise IntegrationError(
                    f"{self.display_name} returned {response.status_code} for {method} {path}: "
                    f"{error_message(response)}",
                    integration=self.integration,
                    upstream_status=response.status_code,
                )

            if response.status_code == 204 or not response.content:
                return {}
            return response.json()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> httpx.Response:
        request = self._client.build_request(
            method, path, params=params, json=payload, headers=await self._auth_headers()
        )
        try:
            response = await self._client.send(request, stream=True)
            try:
                body = await read_limited(
                    response, self.max_response_bytes, integration=self.integration
                )
            finally:
                await response.aclose()
            return buffered(response, body)
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                f"{self.display_name} request timed out: {method} {path}",
                integration=self.integration,
            ) from exc
        except httpx.RequestError as exc:
            raise IntegrationError(
                f"Could not reach {self.display_name}: {exc.__class__.__name__}",
                integration=self.integration,
            ) from exc

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        # GitHub signals primary/secondary rate limits with 403 plus these headers.
        return response.status_code == 403 and (
            "retry-after" in response.headers
            or response.headers.get("x-ratelimit-remaining") == "0"
        )

    @staticmethod
    def _rate_limit_wait(response: httpx.Response) -> float | None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            if retry_after.strip().isdigit():
                return float(retry_after)
            try:
                return max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (TypeError, ValueError):
                return None
        reset = response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return max(0.0, float(reset) - time.time())
        return 1.0 if response.status_code == 429 else None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, payload=payload)

    async def put(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("PUT", path, payload=payload)

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def close(self) -> None:
        await self._client.aclose()
