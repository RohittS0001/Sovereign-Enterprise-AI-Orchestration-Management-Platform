"""An isolated, fully seeded platform instance for evaluations.

In-memory database, demo integrations, one user per role, the demo knowledge base and the
PHX project. Never touches real services or the developer's database.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date

from backend.app.core.config import Settings
from backend.app.core.security.passwords import hash_password
from backend.app.core.security.permissions import Principal, Role
from backend.app.db.models import Project, User, Visibility
from backend.app.db.session import Database
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.knowledge.embeddings import Embedder, HashingEmbedder, build_embedder
from backend.app.domains.knowledge.service import KnowledgeService
from backend.app.integrations.demo.seed import (
    DEMO_DOCUMENTS,
    DEMO_PROJECT_KEY,
    PHX_CONFIG,
    read_document,
)
from backend.app.integrations.hub import IntegrationHub, build_integration_hub
from backend.app.tools.analytics import AnalyticsTools
from backend.app.tools.catalog import build_tool_registry
from backend.app.tools.knowledge import KnowledgeTools
from backend.app.tools.registry import ToolRegistry

EVAL_JWT_SECRET = "evaluation-only-jwt-secret-0123456789abcdef"  # noqa: S105


def eval_settings(**overrides: object) -> Settings:
    """Demo mode + in-memory DB. LLM/embedding settings still come from env/.env."""
    values = {
        "environment": "test",
        "log_level": "WARNING",
        "demo_mode": True,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret_key": EVAL_JWT_SECRET,
        "tool_retry_backoff_seconds": 0,
        "report_schedule_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


@dataclass
class EvalEnvironment:
    settings: Settings
    database: Database
    hub: IntegrationHub
    embedder: Embedder | None
    collector: SnapshotCollector
    registry: ToolRegistry
    principals: dict[Role, Principal] = field(default_factory=dict)

    def knowledge(self, session) -> KnowledgeService:
        return KnowledgeService(session, self.settings, self.embedder)


@asynccontextmanager
async def environment(
    *, real_embeddings: bool = False, demo_today: date | None = None, **overrides: object
) -> AsyncIterator[EvalEnvironment]:
    settings = eval_settings(**overrides)
    database = Database(settings.database_url)
    await database.create_all()
    hub = build_integration_hub(settings, demo_today=demo_today)
    embedder = (
        build_embedder(settings)
        if real_embeddings
        else HashingEmbedder(settings.embedding_dimensions)
    )
    collector = SnapshotCollector(hub, settings)
    registry = build_tool_registry(
        settings,
        hub,
        knowledge_tools=KnowledgeTools(database, settings, embedder),
        analytics_tools=AnalyticsTools(database, collector),
    )
    env = EvalEnvironment(settings, database, hub, embedder, collector, registry)
    try:
        async with database.session() as session:
            for role in Role:
                user = User(email=f"{role.value}@example.com", full_name=role.value, role=role,
                            hashed_password=hash_password("Eval!Passw0rd123"))  # fmt: skip
                session.add(user)
                await session.flush()
                env.principals[role] = Principal.human(user.id, user.email, role)
            session.add(Project(key=DEMO_PROJECT_KEY, **PHX_CONFIG))
            await session.commit()

            admin = env.principals[Role.ADMIN]
            for filename, title, visibility, roles in DEMO_DOCUMENTS:
                await env.knowledge(session).ingest(
                    admin, title=title, text=read_document(filename),
                    source_type="demo", visibility=Visibility(visibility), allowed_roles=roles,
                )  # fmt: skip
        yield env
    finally:
        if embedder is not None:
            await embedder.close()
        await hub.close()
        await database.dispose()
