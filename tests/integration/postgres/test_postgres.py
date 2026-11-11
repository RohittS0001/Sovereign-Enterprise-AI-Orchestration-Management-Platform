"""PostgreSQL-specific behaviour: migrations, pgvector + full-text search, audit chain, UTC.

Skipped unless TEST_POSTGRES_URL points at an empty PostgreSQL database with pgvector
(CI provides one; locally: `docker compose up -d db` and
TEST_POSTGRES_URL=postgresql://sovereign:sovereign@localhost:5432/sovereign). The schema is
dropped and re-created for each test.
"""

import argparse
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.security.passwords import hash_password
from backend.app.core.security.permissions import Principal, Role
from backend.app.db.models import AuditEvent, User, Visibility
from backend.app.db.session import Database
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.knowledge.embeddings import HashingEmbedder
from backend.app.domains.knowledge.service import KnowledgeService
from backend.app.integrations.demo.seed import DEMO_DOCUMENTS, read_document

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL", "")
ROOT = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set"),
]


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.cmd_opts = argparse.Namespace(x=[f"url={POSTGRES_URL}"])
    return config


@pytest.fixture
def migrated() -> None:
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture
async def database(migrated):
    db = Database(POSTGRES_URL)
    yield db
    await db.dispose()


async def make_principal(session, role: Role) -> Principal:
    user = User(email=f"{role.value}@example.com", full_name=role.value, role=role,
                hashed_password=hash_password("Pg!Passw0rd1234"))  # fmt: skip
    session.add(user)
    await session.flush()
    return Principal.human(user.id, user.email, role)


def test_migrations_round_trip_without_drift() -> None:
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)  # models and migrations agree on PostgreSQL too
    command.downgrade(config, "base")
    command.upgrade(config, "head")


async def test_hybrid_search_uses_pgvector_and_full_text(database) -> None:
    settings = Settings(_env_file=None, environment="test", jwt_secret_key="k" * 40)
    embedder = HashingEmbedder(settings.embedding_dimensions)

    async with database.session() as session:
        assert session.get_bind().dialect.name == "postgresql"
        admin = await make_principal(session, Role.ADMIN)
        member = await make_principal(session, Role.MEMBER)
        pm = await make_principal(session, Role.PROJECT_MANAGER)
        await session.commit()
        service = KnowledgeService(session, settings, embedder)
        for filename, title, visibility, roles in DEMO_DOCUMENTS:
            await service.ingest(admin, title=title, text=read_document(filename), source_type="demo",
                                 visibility=Visibility(visibility), allowed_roles=roles)  # fmt: skip

        hits = await service.search(member, "what if the vendor gives no fix date", top_k=3)
        member_contract = await service.search(member, "service credit SLA breach penalties")
        pm_contract = await service.search(pm, "service credit SLA breach penalties")

    assert hits[0].title == "Incident Escalation Policy"
    assert "Vendor incidents" in (hits[0].heading or "")
    assert set(hits[0].matched_by) == {"semantic", "keyword"}  # both PG candidate queries ran
    assert all(h.title != "Gateway Vendor Contract Summary" for h in member_contract)
    assert pm_contract and pm_contract[0].title == "Gateway Vendor Contract Summary"


async def test_audit_chain_and_utc_timestamps(database) -> None:
    async with database.session() as session:
        principal = await make_principal(session, Role.ADMIN)
        audit = AuditService(session)
        for i in range(3):
            await audit.record(
                "test.event", status="success", principal=principal, details={"i": i}
            )
        await session.commit()

    async with database.session() as session:
        verification = await AuditService(session).verify_chain()
        event = await session.scalar(select(AuditEvent).order_by(AuditEvent.seq))

    assert verification.valid and verification.checked == 3
    assert (
        event.occurred_at.utcoffset() is not None
        and event.occurred_at.utcoffset().total_seconds() == 0
    )


async def test_only_one_replica_holds_the_scheduler_lock(database) -> None:
    from backend.app.domains.reporting.scheduler import leader_lock

    async with leader_lock(database) as first:
        other = Database(POSTGRES_URL)  # another replica: its own engine and connections
        try:
            async with leader_lock(other) as second:
                assert first is True and second is False
        finally:
            await other.dispose()
    async with leader_lock(database) as again:  # released when the leader's run ended
        assert again is True


async def test_hnsw_index_is_created_and_used_by_search(database) -> None:
    from backend.app.domains.knowledge.vector_index import create_index, existing_indexes

    embedder = HashingEmbedder(64)
    settings = Settings(_env_file=None, jwt_secret_key="k" * 40, rag_vector_index_dimensions=64)
    async with database.engine.connect() as connection:
        name = await create_index(connection, embedder.model, 64)
        assert name in await existing_indexes(connection)
    async with database.session() as session:
        admin = await make_principal(session, Role.ADMIN)
        await session.commit()
        service = KnowledgeService(session, settings, embedder)
        await service.ingest(admin, title="Rollback runbook", text="Roll back the payments API "
                             "when the error rate stays above two percent.", source_type="demo",
                             visibility=Visibility.ORGANIZATION, allowed_roles=[])  # fmt: skip
        hits = await service.search(admin, "roll back payments error rate", top_k=1)
    assert hits and hits[0].title == "Rollback runbook" and "semantic" in hits[0].matched_by
