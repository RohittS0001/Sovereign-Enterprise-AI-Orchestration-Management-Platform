from typing import Any

from pydantic import BaseModel, Field, ValidationError

from backend.app.core.config.settings import Settings
from backend.app.core.context import get_principal
from backend.app.core.exceptions.exceptions import AppException
from backend.app.db.session import Database
from backend.app.mcp.schemas.common import ToolResult, ToolStatus
from backend.app.retrieval.embeddings.embedders import Embedder
from backend.app.retrieval.reranking.reranker import HttpReranker
from backend.app.retrieval.service import KnowledgeService


class SearchKnowledgeInput(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="What to look for, in plain words")
    top_k: int = Field(default=5, ge=1, le=10)


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
                ).search(principal, inputs.query, top_k=inputs.top_k)
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
