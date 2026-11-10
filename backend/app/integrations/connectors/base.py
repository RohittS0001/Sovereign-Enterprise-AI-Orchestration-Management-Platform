"""Common building blocks for connectors: the HTTP client with pluggable auth, models.

Every connector normalizes its system's records into these models, so the agent, the
analytics and the UI see one shape per kind of data whatever the vendor ("no lock-in").
Personal data is kept to what project management needs (names, not emails/phones).
"""

import re
from datetime import date, datetime
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from backend.app.integrations.common.http import HttpIntegrationClient

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

    async def get_bytes(self, url: str, max_bytes: int = 20_000_000) -> bytes:
        """Download a file (e.g. a PDF from SharePoint), following redirects, size-capped."""
        async with self._client.stream("GET", url, headers=await self._auth_headers(),
                                       follow_redirects=True) as response:  # fmt: skip
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError(f"File larger than {max_bytes // 1_000_000} MB")
            return bytes(data)

    async def get_text(self, url: str) -> str:
        """Download a text body (e.g. a transcript), following redirects, size-capped."""
        response = await self._client.get(url, headers=await self._auth_headers(),
                                          follow_redirects=True)  # fmt: skip
        response.raise_for_status()
        return response.text[:2_000_000]


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


class SourceDocument(BaseModel):
    source: str
    id: str
    title: str
    text: str
    url: str | None = None
    updated: date | None = None
    restricted: bool = False  # the source limits who may read it
    content: bytes | None = None  # raw file (PDF, DOCX, ...) when text is empty
    filename: str | None = None
