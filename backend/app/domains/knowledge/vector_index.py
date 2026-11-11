"""Approximate nearest-neighbour (HNSW) index for pgvector, per embedding model.

The ``embedding`` column is dimension-less so several embedding models can coexist (each
chunk records its model). pgvector can only index vectors of a fixed size, so the index is a
*partial expression index* for one model and dimension::

    CREATE INDEX ix_chunks_hnsw_<dims>_<model hash> ON document_chunks
      USING hnsw ((embedding::vector(<dims>)) vector_cosine_ops)
      WHERE embedding_model = '<model>'

and search casts the column the same way (``RAG_VECTOR_INDEX_DIMENSIONS``) so the planner
can use it. Without the index, dense search is an exact scan: fine for thousands of chunks,
slow for millions. Created by ``python -m scripts.vector_index`` (not by Alembic: it depends
on the deployment's embedding model), and ignored by ``alembic check``.
"""

import hashlib
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

INDEX_PREFIX = "ix_chunks_hnsw_"
_MODEL = re.compile(r"^[A-Za-z0-9._:/@-]{1,200}$")


def index_name(model: str, dimensions: int) -> str:
    digest = hashlib.sha256(model.encode()).hexdigest()[:10]
    return f"{INDEX_PREFIX}{dimensions}_{digest}"


def create_index_sql(model: str, dimensions: int, *, m: int = 16, ef_construction: int = 64) -> str:
    """The DDL for one model. The model name is validated (it becomes a SQL literal)."""
    if not _MODEL.fullmatch(model):
        raise ValueError(f"Unexpected embedding model name: {model!r}")
    if not 16 <= dimensions <= 2000:  # pgvector's HNSW limit for `vector` is 2000
        raise ValueError("HNSW supports 16–2000 dimensions")
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name(model, dimensions)} "
        f"ON document_chunks USING hnsw ((embedding::vector({int(dimensions)})) "
        f"vector_cosine_ops) WITH (m = {int(m)}, ef_construction = {int(ef_construction)}) "
        f"WHERE embedding_model = '{model}'"
    )


async def create_index(connection: AsyncConnection, model: str, dimensions: int) -> str:
    """Create the index (CONCURRENTLY: no write lock, so it needs autocommit)."""
    await connection.execution_options(isolation_level="AUTOCOMMIT")
    await connection.execute(text(create_index_sql(model, dimensions)))
    return index_name(model, dimensions)


async def existing_indexes(connection: AsyncConnection) -> list[str]:
    rows = await connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'document_chunks'")
    )
    return sorted(name for (name,) in rows if name.startswith(INDEX_PREFIX))
