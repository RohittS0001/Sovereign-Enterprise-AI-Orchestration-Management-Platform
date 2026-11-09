from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.metrics import router as metrics_router
from backend.app.api.v1.agent import router as agent_router
from backend.app.api.v1.approvals import router as approvals_router
from backend.app.api.v1.audit import router as audit_router
from backend.app.api.v1.auth import router as auth_router
from backend.app.api.v1.health import router as health_router
from backend.app.api.v1.integrations import router as integrations_router
from backend.app.api.v1.knowledge import router as knowledge_router
from backend.app.api.v1.projects import router as projects_router
from backend.app.api.v1.reports import router as reports_router
from backend.app.api.v1.usage import router as usage_router
from backend.app.api.v1.users import router as users_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.error_handlers import register_exception_handlers
from backend.app.core.logging import configure_logging, get_logger
from backend.app.core.middleware.request_context import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from backend.app.core.middleware.security_headers import SecurityHeadersMiddleware
from backend.app.core.observability import configure_tracing
from backend.app.core.security.rate_limit import RateLimiter
from backend.app.db.session import Database
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.knowledge.embeddings import build_embedder
from backend.app.domains.knowledge.reranker import HttpReranker
from backend.app.domains.reporting.scheduler import ReportScheduler
from backend.app.integrations.hub import build_integration_hub
from backend.app.llm.base import AgentModels
from backend.app.llm.factory import build_agent_models
from backend.app.tools.analytics import AnalyticsTools
from backend.app.tools.catalog import build_tool_registry
from backend.app.tools.knowledge import KnowledgeTools
from backend.app.tools.registry import ToolRegistry

logger = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    *,
    agent_models: AgentModels | None = None,
    tool_registry: ToolRegistry | None = None,
) -> FastAPI:
    """Build the FastAPI application. Tests inject settings, database, models and tools."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_tracing(settings)

    # The app only cleans up resources it created; injected ones belong to the caller.
    owns_database = database is None
    owns_models = agent_models is None
    if database is None and settings.database_url:
        database = Database(settings.database_url, echo=settings.database_echo)
    if agent_models is None:
        agent_models = build_agent_models(settings)
    integrations = build_integration_hub(settings)
    embedder = build_embedder(settings)
    reranker = HttpReranker(settings.reranker_url) if settings.reranker_url else None
    collector = SnapshotCollector(integrations, settings)
    if tool_registry is None:
        knowledge_tools = (
            KnowledgeTools(database, settings, embedder, reranker) if database is not None else None
        )
        analytics_tools = AnalyticsTools(database, collector) if database is not None else None
        tool_registry = build_tool_registry(
            settings, integrations, knowledge_tools=knowledge_tools, analytics_tools=analytics_tools
        )
    scheduler = (
        ReportScheduler(database, collector, settings, agent_models)
        if settings.report_schedule_enabled and database is not None
        else None
    )

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
        if agent_models is None:
            logger.warning("No LLM configured (LLM1_MODEL_NAME); the agent endpoint is disabled")
        if settings.demo_mode:
            logger.warning("DEMO_MODE is on: integrations serve synthetic data, not real systems")
        logger.info("Agent tools available", extra={"tools": tool_registry.list_tools()})
        if scheduler is not None:
            scheduler.start()
        yield
        if scheduler is not None:
            await scheduler.stop()
        if owns_models and agent_models is not None:
            await agent_models.close()
        await integrations.close()
        if embedder is not None:
            await embedder.close()
        if reranker is not None:
            await reranker.close()
        if owns_database and database is not None:
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
    app.state.agent_models = agent_models
    app.state.tool_registry = tool_registry
    app.state.integrations = integrations
    app.state.embedder = embedder
    app.state.reranker = reranker
    app.state.analytics_collector = collector
    app.state.report_scheduler = scheduler

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
    app.include_router(metrics_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(agent_router)
    app.include_router(approvals_router)
    app.include_router(audit_router)
    app.include_router(integrations_router)
    app.include_router(knowledge_router)
    app.include_router(projects_router)
    app.include_router(reports_router)
    app.include_router(usage_router)

    return app


app = create_app()
