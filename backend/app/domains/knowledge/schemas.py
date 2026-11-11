import re
import uuid
from datetime import UTC, date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.core.security.permissions import Role
from backend.app.db.models import Visibility
from backend.app.domains.knowledge.service import SearchFilters

SOURCE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class DocumentTextCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2_000_000)
    visibility: Visibility = Visibility.ORGANIZATION
    allowed_roles: list[Role] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=20)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    source_type: str
    filename: str | None
    mime_type: str | None
    visibility: Visibility
    allowed_roles: list[str]
    tags: list[str]
    owner_id: uuid.UUID | None
    char_count: int
    chunk_count: int
    embedding_model: str | None
    project_key: str | None = None
    occurred_at: datetime | None = None
    created_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentRead]
    total: int
    limit: int
    offset: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=6, ge=1, le=20)
    sources: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Only these sources (jira, slack, email, upload, …)",
    )
    project_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,31}$")
    since: date | None = None
    until: date | None = None

    @field_validator("sources")
    @classmethod
    def _sources(cls, sources: list[str]) -> list[str]:
        for source in sources:
            if not SOURCE.fullmatch(source):
                raise ValueError(f"invalid source {source!r}")
        return sources

    def filters(self) -> SearchFilters | None:
        if not (self.sources or self.project_key or self.since or self.until):
            return None
        return SearchFilters(
            sources=tuple(self.sources),
            project=self.project_key,
            since=datetime.combine(self.since, time.min, UTC) if self.since else None,
            until=datetime.combine(self.until, time.max, UTC) if self.until else None,
        )


class SearchResult(BaseModel):
    document_id: str
    title: str
    section: str | None
    chunk: int
    citation: str
    score: float
    matched_by: list[str]
    source: str | None = None
    project: str | None = None
    date: str | None = None
    text: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
