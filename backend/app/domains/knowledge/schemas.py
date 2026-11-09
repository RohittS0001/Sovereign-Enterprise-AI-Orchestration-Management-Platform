import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.security.permissions import Role
from backend.app.db.models import Visibility


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
    created_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentRead]
    total: int
    limit: int
    offset: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=6, ge=1, le=20)


class SearchResult(BaseModel):
    document_id: str
    title: str
    section: str | None
    chunk: int
    citation: str
    score: float
    matched_by: list[str]
    text: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
