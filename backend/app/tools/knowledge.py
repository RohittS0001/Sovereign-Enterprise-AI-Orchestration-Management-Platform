from datetime import UTC, date, datetime, time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from backend.app.core.config import Settings
from backend.app.core.context import get_principal
from backend.app.core.errors import AppException
from backend.app.db.session import Database
from backend.app.domains.knowledge.embeddings import Embedder
from backend.app.domains.knowledge.reranker import HttpReranker
from backend.app.domains.knowledge.service import KnowledgeService, SearchFilters
from backend.app.tools.base import ToolResult, ToolStatus


class SearchKnowledgeInput(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="What to look for, in plain words")
    top_k: int = Field(default=5, ge=1, le=10)
    sources: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Only these sources, e.g. ['slack'], ['jira','github'], ['email'], "
        "['upload','demo'] for policy documents. Empty = everything.",
    )
    project_key: str | None = Field(default=None, description="Only records of this project")
    since: date | None = Field(default=None, description="Only records from this date on")


class KnowledgeTools:
    """Agent access to the internal knowledge base, filtered by the user's access."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        embedder: Embedder | None,
        reranker: HttpReranker | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.embedder = embedder
        self.reranker = reranker

    async def search(self, payload: dict[str, Any]) -> ToolResult:
        principal = get_principal()
        if principal is None:
            # Never search without knowing whose permissions apply.
            return ToolResult(status=ToolStatus.DENIED, error="No acting user for knowledge search")
        try:
            inputs = SearchKnowledgeInput.model_validate(payload)
        except ValidationError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=f"Invalid search input: {exc}")

        try:
            async with self.database.session() as session:
                hits = await KnowledgeService(
                    session, self.settings, self.embedder, self.reranker
                ).search(
                    principal,
                    inputs.query,
                    top_k=inputs.top_k,
                    filters=SearchFilters(
                        sources=tuple(inputs.sources),
                        project=inputs.project_key,
                        since=datetime.combine(inputs.since, time.min, UTC)
                        if inputs.since
                        else None,
                    )
                    if (inputs.sources or inputs.project_key or inputs.since)
                    else None,
                )
        except AppException as exc:
            return ToolResult(
                status=ToolStatus.ERROR, error=f"Knowledge search failed: {exc.message}"
            )

        return ToolResult(
            status=ToolStatus.SUCCESS,
            data={
                "query": inputs.query,
                "results": [hit.as_dict() for hit in hits],
                "note": None if hits else "No accessible documents matched.",
            },
        )
