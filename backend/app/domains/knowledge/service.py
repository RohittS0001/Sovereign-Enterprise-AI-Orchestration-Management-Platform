"""Knowledge base: ingestion and permission-aware hybrid search.

Access control is applied *inside* the candidate queries, never after ranking, so a
user can neither see nor infer documents they are not allowed to read.

Search = dense (embeddings) + sparse (keywords) → reciprocal rank fusion → at most N
chunks per document → optional cross-encoder rerank. On PostgreSQL both candidate
lists come from the database (pgvector ``<=>`` and full-text ``ts_rank_cd``); on other
databases (SQLite in tests/dev) permitted chunks are ranked in Python.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, func, literal_column, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from backend.app.core.logging import get_logger
from backend.app.core.security.permissions import Permission, Principal, Role
from backend.app.db.models import Document, DocumentChunk, Visibility
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.knowledge.chunking import chunk_text
from backend.app.domains.knowledge.embeddings import Embedder, cosine, tokenize
from backend.app.domains.knowledge.ranking import bm25_scores, cap_per_group, reciprocal_rank_fusion
from backend.app.domains.knowledge.reranker import HttpReranker

logger = get_logger(__name__)

# Safety valve for the in-Python path; PostgreSQL never loads the whole corpus.
_MAX_PYTHON_CANDIDATES = 20000


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    heading: str | None
    ordinal: int
    text: str
    score: float
    matched_by: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        return f"{self.title} › {self.heading}" if self.heading else self.title

    def as_dict(self, max_chars: int = 1500) -> dict[str, Any]:
        text = self.text if len(self.text) <= max_chars else self.text[:max_chars] + "…"
        return {
            "document_id": str(self.document_id),
            "title": self.title,
            "section": self.heading,
            "chunk": self.ordinal,
            "citation": self.citation,
            "score": round(self.score, 4),
            "matched_by": self.matched_by,
            "text": text,
        }


def access_filter(principal: Principal) -> ColumnElement[bool]:
    """SQL condition: documents this principal may read."""
    if principal.role == Role.ADMIN:
        return true()
    return or_(
        Document.visibility == Visibility.ORGANIZATION,
        Document.owner_id == principal.user_id,
        and_(
            Document.visibility == Visibility.ROLES,
            Document.allowed_roles_csv.like(f"%,{principal.role.value},%"),
        ),
    )


class KnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        embedder: Embedder | None,
        reranker: HttpReranker | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.embedder = embedder
        self.reranker = reranker
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ documents

    async def ingest(
        self,
        principal: Principal,
        *,
        title: str,
        text: str,
        source_type: str,
        filename: str | None = None,
        mime_type: str | None = None,
        visibility: Visibility = Visibility.ORGANIZATION,
        allowed_roles: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Document:
        if not principal.has(Permission.KNOWLEDGE_MANAGE):
            raise AuthorizationError("You cannot add documents to the knowledge base")

        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = await self.session.scalar(
            select(Document.id).where(Document.checksum == checksum)
        )
        if existing is not None:
            raise ConflictError(
                "This document is already in the knowledge base",
                details={"document_id": str(existing)},
            )

        chunks = chunk_text(
            text, size=self.settings.rag_chunk_size, overlap=self.settings.rag_chunk_overlap
        )
        vectors: list[list[float] | None] = [None] * len(chunks)
        if self.embedder is not None and chunks:
            vectors = list(
                await self.embedder.embed(
                    [self._embedding_input(title, c.heading, c.text) for c in chunks]
                )
            )
        model = self.embedder.model if self.embedder is not None else None

        document = Document(
            id=uuid.uuid4(),
            title=title,
            source_type=source_type,
            filename=filename,
            mime_type=mime_type,
            checksum=checksum,
            owner_id=principal.user_id,
            visibility=visibility,
            tags=tags or [],
            char_count=len(text),
            chunk_count=len(chunks),
            embedding_model=model,
        )
        document.allowed_roles = allowed_roles or []
        document.chunks = [
            DocumentChunk(
                ordinal=c.ordinal,
                heading=c.heading[:300] if c.heading else None,
                text=c.text,
                char_start=c.char_start,
                embedding=vector,
                embedding_model=model if vector is not None else None,
            )
            for c, vector in zip(chunks, vectors, strict=True)
        ]
        self.session.add(document)
        await self.session.flush()
        await self.audit.record(
            actions.KNOWLEDGE_INGEST,
            status=actions.SUCCESS,
            principal=principal,
            resource_type="document",
            resource_id=str(document.id),
            details={"title": title, "chunks": len(chunks), "visibility": visibility.value},
        )
        await self.session.commit()
        logger.info(
            "Document ingested", extra={"document_id": str(document.id), "chunks": len(chunks)}
        )
        return document

    @staticmethod
    def _embedding_input(title: str, heading: str | None, text: str) -> str:
        # Prefixing title/section gives chunks context they lack on their own.
        return f"{title}\n{heading or ''}\n{text}"

    async def get(self, principal: Principal, document_id: uuid.UUID) -> Document:
        document = await self.session.scalar(
            select(Document).where(Document.id == document_id, access_filter(principal))
        )
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def list_documents(
        self, principal: Principal, *, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        condition = access_filter(principal)
        total = await self.session.scalar(
            select(func.count()).select_from(Document).where(condition)
        )
        documents = await self.session.scalars(
            select(Document)
            .where(condition)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(documents), total or 0

    async def delete(self, principal: Principal, document_id: uuid.UUID) -> None:
        document = await self.get(principal, document_id)
        if principal.role != Role.ADMIN and not (
            principal.has(Permission.KNOWLEDGE_MANAGE) and document.owner_id == principal.user_id
        ):
            raise AuthorizationError("Only the owner or an admin can delete this document")
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        await self.session.delete(document)
        await self.audit.record(
            actions.KNOWLEDGE_DELETE,
            status=actions.SUCCESS,
            principal=principal,
            resource_type="document",
            resource_id=str(document_id),
            details={"title": document.title},
        )
        await self.session.commit()

    # ------------------------------------------------------------------ search

    def _chunk_query(self, principal: Principal) -> Select:
        return (
            select(DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.ordinal,
                   DocumentChunk.heading, DocumentChunk.text, Document.title)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(access_filter(principal))
        )  # fmt: skip

    async def search(
        self, principal: Principal, query: str, *, top_k: int | None = None
    ) -> list[SearchHit]:
        if not principal.has(Permission.KNOWLEDGE_READ):
            raise AuthorizationError("You cannot search the knowledge base")
        query = " ".join(query.split())
        if not query:
            return []
        top_k = top_k or self.settings.rag_top_k

        query_vector = None
        if self.embedder is not None:
            [query_vector] = await self.embedder.embed([query])

        if self.session.get_bind().dialect.name == "postgresql":
            rows, dense, sparse = await self._postgres_candidates(principal, query, query_vector)
        else:
            rows, dense, sparse = await self._python_candidates(principal, query, query_vector)

        fused = reciprocal_rank_fusion([r for r in (dense, sparse) if r])
        ordered = cap_per_group(
            [chunk_id for chunk_id, _ in fused],
            {chunk_id: rows[chunk_id]["document_id"] for chunk_id in rows},
            self.settings.rag_max_chunks_per_document,
        )
        scores = dict(fused)

        if self.reranker is not None and len(ordered) > 1:
            pool = ordered[: top_k * 3]
            order = await self.reranker.rerank(query, [rows[c]["text"] for c in pool])
            if order:
                ordered = [pool[i] for i in order] + [c for c in ordered if c not in pool]

        dense_set, sparse_set = set(dense), set(sparse)
        return [
            SearchHit(
                chunk_id=chunk_id,
                document_id=rows[chunk_id]["document_id"],
                title=rows[chunk_id]["title"],
                heading=rows[chunk_id]["heading"],
                ordinal=rows[chunk_id]["ordinal"],
                text=rows[chunk_id]["text"],
                score=scores.get(chunk_id, 0.0),
                matched_by=[
                    name
                    for name, hits in (("semantic", dense_set), ("keyword", sparse_set))
                    if chunk_id in hits
                ],
            )
            for chunk_id in ordered[:top_k]
        ]

    async def _python_candidates(
        self, principal: Principal, query: str, query_vector: list[float] | None
    ) -> tuple[dict, list, list]:
        statement = (
            self._chunk_query(principal)
            .add_columns(DocumentChunk.embedding, DocumentChunk.embedding_model)
            .limit(_MAX_PYTHON_CANDIDATES)
        )
        result = (await self.session.execute(statement)).all()
        rows = {r.id: r._asdict() for r in result}
        ids = list(rows)
        limit = self.settings.rag_candidate_k

        keyword = bm25_scores(query, [rows[i]["text"] for i in ids])
        sparse = [
            ids[n] for n in sorted(range(len(ids)), key=lambda n: -keyword[n]) if keyword[n] > 0
        ][:limit]

        dense: list = []
        if query_vector is not None:
            model = self.embedder.model if self.embedder else None
            scored = [
                (cosine(query_vector, rows[i]["embedding"]), i)
                for i in ids
                if rows[i]["embedding"] is not None and rows[i]["embedding_model"] == model
            ]
            dense = [i for score, i in sorted(scored, key=lambda s: -s[0]) if score > 0][:limit]
        return rows, dense, sparse

    async def _postgres_candidates(
        self, principal: Principal, query: str, query_vector: list[float] | None
    ) -> tuple[dict, list, list]:
        limit = self.settings.rag_candidate_k
        rows: dict = {}
        dense: list = []

        if query_vector is not None and self.embedder is not None:
            dense_statement = (
                self._chunk_query(principal)
                .where(DocumentChunk.embedding_model == self.embedder.model)
                .order_by(DocumentChunk.embedding.op("<=>")(query_vector))
                .limit(limit)
            )
            for r in (await self.session.execute(dense_statement)).all():
                rows[r.id] = r._asdict()
                dense.append(r.id)

        # OR the terms, like BM25 on other databases: websearch_to_tsquery ANDs every word and
        # the 'simple' config keeps stopwords, so natural questions would match nothing.
        # tokenize() yields only [a-z0-9]+, so no tsquery syntax can be injected.
        sparse = []
        terms = sorted(set(tokenize(query)))
        if terms:
            # A literal config (not a bind parameter) so the planner can use the GIN index
            # ix_document_chunks_fts, created on to_tsvector('simple', text).
            config = literal_column("'simple'::regconfig")
            tsvector = func.to_tsvector(config, DocumentChunk.text)
            tsquery = func.to_tsquery(config, " | ".join(terms))
            sparse_statement = (
                self._chunk_query(principal)
                .where(tsvector.op("@@")(tsquery))
                .order_by(func.ts_rank_cd(tsvector, tsquery).desc())
                .limit(limit)
            )
            for r in (await self.session.execute(sparse_statement)).all():
                rows.setdefault(r.id, r._asdict())
                sparse.append(r.id)
        return rows, dense, sparse
