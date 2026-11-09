"""Async database engine and session factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from backend.app.core.logging import get_logger

logger = get_logger(__name__)


_ASYNC_DRIVERS = {
    "postgresql": "postgresql+psycopg",
    "postgres": "postgresql+psycopg",
    "sqlite": "sqlite+aiosqlite",
}


def normalize_database_url(url: str) -> str:
    """Pick the async driver for plain ``postgresql://`` / ``sqlite://`` URLs.

    Only the scheme is swapped; the rest is left verbatim (re-rendering the URL would
    percent-encode values such as ``:memory:`` or passwords).
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        raise ValueError("DATABASE_URL must look like 'driver://...'")
    return f"{_ASYNC_DRIVERS.get(scheme, scheme)}://{rest}"


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = normalize_database_url(url)
        parsed = make_url(self.url)

        kwargs: dict = {"echo": echo}
        if parsed.get_backend_name() == "sqlite":
            kwargs["connect_args"] = {"check_same_thread": False}
            if parsed.database in (None, "", ":memory:"):
                # One shared connection, otherwise every session sees an empty database.
                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)

        self.engine: AsyncEngine = create_async_engine(self.url, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def backend(self) -> str:
        return self.engine.url.get_backend_name()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("Database ping failed", exc_info=True)
            return False

    async def create_all(self) -> None:
        """Create tables directly. Tests only; real databases use Alembic migrations."""
        from backend.app.db import models  # noqa: F401  (register models)
        from backend.app.db.base import Base

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
