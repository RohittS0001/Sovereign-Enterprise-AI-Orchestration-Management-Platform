from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.agent import router as agent_router
from backend.app.api.health import router as health_router
from backend.app.core.config.settings import Settings, get_settings
from backend.app.core.exceptions.handlers import register_exception_handlers
from backend.app.core.logging.logger import configure_logging, get_logger
from backend.app.core.middleware.request_context import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. Tests pass their own ``Settings``."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Starting %s",
            settings.app_name,
            extra={"environment": settings.environment, "version": settings.version},
        )
        yield
        logger.info("Shutting down %s", settings.app_name)

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last so it runs first and every response (including errors) gets an ID.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(agent_router)

    return app


app = create_app()
