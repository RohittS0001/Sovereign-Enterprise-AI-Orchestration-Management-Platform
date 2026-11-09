from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.agent import router as agent_router
from backend.app.api.auth import router as auth_router
from backend.app.api.health import router as health_router
from backend.app.api.users import router as users_router
from backend.app.core.config.settings import Settings, get_settings
from backend.app.core.exceptions.handlers import register_exception_handlers
from backend.app.core.logging.logger import configure_logging, get_logger
from backend.app.core.middleware.request_context import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from backend.app.core.middleware.security_headers import SecurityHeadersMiddleware
from backend.app.core.security.rate_limit import RateLimiter
from backend.app.db.session import Database

logger = get_logger(__name__)


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    """Build the FastAPI application. Tests pass their own settings and database."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if database is None and settings.database_url:
        database = Database(settings.database_url, echo=settings.database_echo)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Starting %s",
            settings.app_name,
            extra={"environment": settings.environment, "version": settings.version},
        )
        if settings.jwt_secret_generated:
            logger.warning(
                "JWT_SECRET_KEY is not set; using a temporary key. "
                "Sessions will not survive a restart."
            )
        if database is None:
            logger.warning("DATABASE_URL is not set; login and user APIs are unavailable")
        elif not await database.ping():
            logger.error("Database is not reachable at startup", extra={"db": database.backend})
        yield
        if database is not None:
            await database.dispose()
        logger.info("Shutting down %s", settings.app_name)

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
    )
    app.state.settings = settings
    app.state.database = database
    app.state.rate_limiter = RateLimiter()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    # Added last so it runs first and every response (including errors) gets an ID.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(agent_router)

    return app


app = create_app()
