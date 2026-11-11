"""Common building blocks for connectors: the HTTP client with pluggable auth, models.

Every connector normalizes its system's records into these models, so the agent, the
analytics and the UI see one shape per kind of data whatever the vendor ("no lock-in").
Personal data is kept to what project management needs (names, not emails/phones).
"""

import re
from datetime import date, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from backend.app.core.errors import IntegrationError
from backend.app.integrations.common.http import HttpIntegrationClient, read_limited

MAX_REDIRECTS = 5
_REDIRECTS = {301, 302, 303, 307, 308}
_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def host_matches(hostname: str | None, domains: tuple[str, ...]) -> bool:
    """True when ``hostname`` is one of ``domains`` or a subdomain of one (label boundary).

    ``zoom.us`` and ``us02web.zoom.us`` match ("zoom.us",); ``evilzoom.us`` and
    ``zoom.us.evil.com`` do not.
    """
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    if not _HOSTNAME.fullmatch(host):
        return False
    wanted = [d.lower().strip(".") for d in domains]
    return any(host == d or host.endswith("." + d) for d in wanted)


def checked_url(url: str, domains: tuple[str, ...]) -> str:
    """Return ``url`` if it is https on the default port, has no credentials and its host is
    one of ``domains`` (or a subdomain); raise ValueError otherwise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Refusing a malformed URL") from exc
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not host_matches(parts.hostname, domains)
    ):
        raise ValueError(f"Refusing to call an unexpected host: {parts.hostname!r}")
    return url


# ------------------------------------------------------------------ client


class TokenProvider(Protocol):
    async def get(self) -> str: ...

    def invalidate(self) -> None: ...


class ConnectorClient(HttpIntegrationClient):
    """HttpIntegrationClient whose auth is a header template filled from a token provider.

    ``auth_header=("Authorization", "Bearer {token}")`` with a ``token_provider`` (OAuth),
    or static ``headers``/``auth`` for API keys and Basic auth.
    """

    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        timeout: float,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | tuple[str, str] | None = None,
        token_provider: TokenProvider | None = None,
        auth_header: tuple[str, str] = ("Authorization", "Bearer {token}"),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.display_name = name
        self.integration = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        self.token_provider = token_provider
        self.auth_header = auth_header
        super().__init__(base_url=base_url, timeout=timeout, headers=headers, auth=auth,
                         transport=transport)  # fmt: skip

    async def _auth_headers(self) -> dict[str, str]:
        if self.token_provider is None:
            return {}
        name, template = self.auth_header
        return {name: template.format(token=await self.token_provider.get())}

    async def _on_unauthorized(self) -> bool:
        if self.token_provider is None:
            return False
        self.token_provider.invalidate()
        return True

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self, path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("POST", path, params=params, payload=payload)

    async def _download(
        self, url: str, max_bytes: int, redirect_hosts: tuple[str, ...] | None
    ) -> httpx.Response:
        """GET ``url`` following redirects by hand: each hop must be https to an allowed host
        (``redirect_hosts``, default: the first host only), and credentials (auth headers,
        static API-key headers, Basic auth) are only ever sent to the first origin."""
        target = self._client.build_request("GET", url).url
        origin = (target.scheme, target.host, target.port)
        allowed = redirect_hosts or (target.host,)
        for _ in range(MAX_REDIRECTS + 1):
            if (target.scheme, target.host, target.port) == origin:
                request = self._client.build_request(
                    "GET", target, headers=await self._auth_headers()
                )
                auth: Any = httpx.USE_CLIENT_DEFAULT
            else:  # a bare request to another host: no client headers, no auth
                request = httpx.Request("GET", target, headers={"Accept": "*/*"})
                auth = None
            try:
                response = await self._client.send(
                    request, auth=auth, stream=True, follow_redirects=False
                )
            except httpx.HTTPError as exc:
                raise IntegrationError(
                    f"Could not download from {self.display_name}: {exc.__class__.__name__}",
                    integration=self.integration,
                ) from exc
            try:
                if response.status_code in _REDIRECTS and "location" in response.headers:
                    nxt = target.join(response.headers["location"])
                    try:
                        checked_url(str(nxt), allowed)
                    except ValueError as exc:
                        raise IntegrationError(
                            f"{self.display_name} redirected to an untrusted host ({nxt.host})",
                            integration=self.integration,
                        ) from exc
                    target = nxt
                    continue
                if response.is_error:
                    raise IntegrationError(
                        f"{self.display_name} returned {response.status_code} for a download",
                        integration=self.integration,
                        upstream_status=response.status_code,
                    )
                body = await read_limited(response, max_bytes, integration=self.integration)
                content_type = response.headers.get("content-type", "")
                return httpx.Response(
                    response.status_code, headers={"content-type": content_type}, content=body
                )
            finally:
                await response.aclose()
        raise IntegrationError(
            f"{self.display_name}: too many redirects", integration=self.integration
        )

    async def get_bytes(
        self,
        url: str,
        max_bytes: int = 20_000_000,
        redirect_hosts: tuple[str, ...] | None = None,
    ) -> bytes:
        """Download a file (e.g. a PDF from SharePoint): streamed, size-capped, safe redirects."""
        return (await self._download(url, max_bytes, redirect_hosts)).content

    async def get_text(
        self,
        url: str,
        max_bytes: int = 2_000_000,
        redirect_hosts: tuple[str, ...] | None = None,
    ) -> str:
        """Download a text body (e.g. a transcript): streamed, size-capped, safe redirects."""
        return (await self._download(url, max_bytes, redirect_hosts)).text


class ConnectorService:
    """Base for connector services: lazy client, close, health check."""

    source = "connector"

    def __init__(self, client_factory: Any) -> None:
        self._client_factory = client_factory
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def health_check(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError


# ------------------------------------------------------------------ helpers

SAFE_ID = re.compile(r"^[A-Za-z0-9._:@/\- ]{1,200}$")


def safe_id(value: str, what: str = "identifier") -> str:
    """Reject identifiers that could alter a URL path or query (traversal, injection)."""
    value = str(value).strip()
    if not SAFE_ID.fullmatch(value) or ".." in value:
        raise ValueError(f"Invalid {what}: {value!r}")
    return value


def to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):  # epoch milliseconds (ClickUp)
        return datetime.fromtimestamp(value / 1000).date()
    text = str(value)
    if text.isdigit() and len(text) >= 12:
        return datetime.fromtimestamp(int(text) / 1000).date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


Category3 = Literal["todo", "in_progress", "done"]


def category_of(status: str) -> Category3:
    s = status.lower()
    if any(w in s for w in ("done", "closed", "complete", "resolved", "merged", "cancel")):
        return "done"
    if any(w in s for w in ("progress", "review", "started", "doing", "active", "testing")):
        return "in_progress"
    return "todo"


# ------------------------------------------------------------------ common models


class WorkItem(BaseModel):
    source: str
    key: str
    title: str
    status: str
    category: Category3
    assignee: str | None = None
    priority: str | None = None
    points: float | None = None
    due: date | None = None
    updated: date | None = None
    url: str | None = None
    project: str | None = None


class PullRequest(BaseModel):
    source: str
    repository: str
    number: int | str
    title: str
    state: str
    author: str | None = None
    updated: date | None = None
    url: str | None = None


class Incident(BaseModel):
    source: str
    id: str
    title: str
    status: str  # triggered | acknowledged | resolved | open | closed
    severity: str | None = None
    service: str | None = None
    created: date | None = None
    url: str | None = None
    resolved: date | None = None


class ErrorGroup(BaseModel):
    source: str
    id: str
    title: str
    level: str | None = None
    events: int = 0
    users: int = 0
    last_seen: date | None = None
    url: str | None = None


class Deployment(BaseModel):
    source: str
    name: str
    status: str  # success | failure | running | degraded | out_of_sync ...
    finished: date | None = None
    url: str | None = None
    detail: str | None = None


class QualityGate(BaseModel):
    source: str
    project: str
    status: str  # OK | WARN | ERROR
    failing: list[str] = Field(default_factory=list)


class LeaveRecord(BaseModel):
    source: str
    employee_id: str
    name: str | None = None
    start: date
    end: date
    type: str | None = None
    status: str | None = None


class BudgetLine(BaseModel):
    source: str
    project: str
    budget: float | None = None
    actual: float | None = None
    currency: str | None = None


class Deal(BaseModel):
    source: str
    id: str
    name: str
    stage: str | None = None
    amount: float | None = None
    close_date: date | None = None
    project_key: str | None = None
    url: str | None = None


class CalendarEvent(BaseModel):
    source: str
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: int = 0
    organizer: str | None = None


class BusySlot(BaseModel):
    person: str
    start: datetime
    end: datetime


class Transcript(BaseModel):
    source: str
    meeting_id: str
    title: str
    held_on: date | None = None
    text: str = ""


CloudHealth = Literal["ok", "warning", "impaired", "stopped", "unknown"]


class CloudResource(BaseModel):
    """Something running (or stored) in a cloud account, attributed to a project by tag."""

    source: str  # aws | azure
    id: str  # ARN / Azure resource id
    name: str
    kind: str  # instance | database | disk | bucket | container | function | network | other
    service: str  # e.g. ec2, rds, s3, Microsoft.Compute/virtualMachines
    size: str | None = None  # instance type / SKU
    region: str | None = None
    state: str | None = None  # running, stopped, available …
    health: CloudHealth = "unknown"
    health_detail: str | None = None
    storage_gb: float | None = None
    created: date | None = None
    environment: str | None = None  # from the environment tag
    purpose: str | None = None  # why it exists: purpose / description tag
    owner: str | None = None  # owner tag
    tags: dict[str, str] = Field(default_factory=dict)
    url: str | None = None


class CostLine(BaseModel):
    service: str
    amount: float


class CostReport(BaseModel):
    source: str
    currency: str = "USD"
    period_start: date
    period_end: date
    total: float
    forecast_month_end: float | None = None
    by_service: list[CostLine] = Field(default_factory=list)


class CloudAlert(BaseModel):
    source: str
    id: str
    title: str
    severity: str | None = None
    state: str  # ALARM / Fired …
    resource: str | None = None
    started: datetime | None = None
    url: str | None = None


class SecurityFinding(BaseModel):
    source: str
    id: str
    title: str
    severity: str  # critical | high | medium | low | informational
    status: str
    resource: str | None = None
    created: date | None = None
    recommendation: str | None = None
    url: str | None = None


class SupportTicket(BaseModel):
    """A customer support ticket (Zendesk …), tied to a project by tag."""

    source: str
    id: str
    subject: str
    status: str
    priority: str | None = None
    created: date | None = None
    updated: date | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None


class ChannelMessage(BaseModel):
    """A message in a team chat channel (Slack, Teams)."""

    source: str
    channel: str
    id: str
    author: str | None = None
    text: str
    sent_at: datetime
    thread_id: str | None = None
    url: str | None = None


class Listing(list):  # type: ignore[type-arg]
    """A list of records plus whether it is the source's *complete* set.

    ``complete=False`` (a page cap was hit, a page failed) tells a sync not to delete local
    copies of records that are merely missing from this partial result.
    """

    def __init__(self, items: Any = (), complete: bool = True) -> None:
        super().__init__(items)
        self.complete = complete


class SourceDocument(BaseModel):
    source: str
    id: str
    title: str
    text: str
    url: str | None = None
    updated: date | None = None
    # True: the source limits who may read it; False: readable by the whole organization;
    # None: the ACL could not be read or mapped, so the sync treats it as restricted.
    restricted: bool | None = None
    content: bytes | None = None  # raw file (PDF, DOCX, ...) when text is empty
    filename: str | None = None
