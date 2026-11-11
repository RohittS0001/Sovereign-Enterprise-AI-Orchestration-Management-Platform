"""Alembic environment: runs migrations with the app's async engine settings."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.config import get_settings
from backend.app.db import models  # noqa: F401  (register models on the metadata)
from backend.app.db.base import Base
from backend.app.db.session import normalize_database_url
from backend.app.db.types import EmbeddingVector

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # `alembic -x url=...` overrides DATABASE_URL (handy for one-off databases).
    url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set; cannot run migrations.")
    return normalize_database_url(url)


def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    # EmbeddingVector renders as pgvector `vector` on PostgreSQL but is JSON-backed in
    # Python; its reflected type never matches literally, so don't report it as drift.
    if isinstance(metadata_column.type, EmbeddingVector):
        return False
    return None  # default comparison for everything else


# Expression indexes created with raw SQL in migrations (models cannot declare them portably).
_SQL_MANAGED_INDEXES = {"ix_document_chunks_fts"}


def _include_object(obj, name, type_, reflected, compare_to):
    # HNSW indexes depend on the deployment's embedding model (scripts/vector_index.py).
    if type_ == "index" and name and name.startswith("ix_chunks_hnsw_"):
        return False
    return not (type_ == "index" and name in _SQL_MANAGED_INDEXES)


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        compare_type=_compare_type,
        include_object=_include_object,
        # SQLite cannot ALTER most things in place; batch mode recreates tables instead.
        render_as_batch=True,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"} if connection is None else {},
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout (``alembic upgrade head --sql``) without a database."""
    _configure(url=_database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
