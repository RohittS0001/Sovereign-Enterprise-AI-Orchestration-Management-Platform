"""Data isolation regressions: document ACLs through sync, search and the knowledge graph,
partial fetches never deleting indexed records, and redaction before external embedding."""

from datetime import date

from pydantic import SecretStr
from sqlalchemy import func, select

from backend.app.core.config import Settings, endpoint_locality
from backend.app.core.security.permissions import Role
from backend.app.db.models import Document, Project
from backend.app.domains.compliance.service import _data_protection
from backend.app.domains.graph.service import build_graph, who_knows
from backend.app.domains.knowledge.embeddings import (
    HashingEmbedder,
    RedactingEmbedder,
    build_embedder,
)
from backend.app.domains.knowledge.indexing import index_project
from backend.app.domains.knowledge.sync import sync_source
from backend.app.integrations.connectors.base import Listing, SourceDocument
from evaluation.environment import environment

TODAY = date(2026, 11, 9)


class Source:
    def __init__(self, documents, complete: bool = True) -> None:
        self.documents, self.complete = documents, complete

    async def list_documents(self):
        return Listing(self.documents, complete=self.complete)


def doc(i: str, text: str, restricted: bool | None) -> SourceDocument:
    return SourceDocument(source="notion", id=i, title=f"Doc {i}", text=text, restricted=restricted)


# ------------------------------------------------------------------ 3. ACLs end to end


async def test_unknown_and_restricted_acls_fail_closed_in_search() -> None:
    async with environment(demo_today=TODAY) as env:
        admin, member = env.principals[Role.ADMIN], env.principals[Role.MEMBER]
        source = Source(
            [
                doc("open", "Quarterly zebra roadmap is public to everyone.", False),
                doc("unknown", "Zebra acquisition target list, board only.", None),
                doc("closed", "Zebra salary bands for the leadership team.", True),
            ]
        )
        async with env.database.session() as session:
            result = await sync_source(session, env.settings, env.embedder, admin, "notion", source)
        assert result.added == 3

        async def titles(principal) -> set[str]:
            async with env.database.session() as session:
                hits = await env.knowledge(session).search(principal, "zebra", top_k=10)
            return {h.title for h in hits}

        assert await titles(member) == {"Doc open"}
        assert {"Doc open", "Doc unknown", "Doc closed"} <= await titles(admin)

        # The admin may opt a source whose ACL cannot be read into organization-wide access.
        opened = env.settings.model_copy(update={"docs_open_sources": ["notion"]})
        source.documents = [doc("unknown", "Zebra acquisition target list, edited.", None)]
        async with env.database.session() as session:
            await sync_source(session, opened, env.embedder, admin, "notion", source)
        assert "Doc unknown" in await titles(member)


async def test_a_partial_listing_never_deletes_synced_documents() -> None:
    async with environment(demo_today=TODAY) as env:
        admin = env.principals[Role.ADMIN]
        full = Source([doc(str(i), f"Runbook number {i} for the ledger.", False) for i in range(3)])
        async with env.database.session() as session:
            await sync_source(session, env.settings, env.embedder, admin, "notion", full)
            capped = Source(full.documents[:1], complete=False)
            partial = await sync_source(
                session, env.settings, env.embedder, admin, "notion", capped
            )
            kept = await session.scalar(
                select(func.count()).select_from(Document).where(Document.source_type == "notion")
            )
            complete = Source(full.documents[:1], complete=True)
            final = await sync_source(
                session, env.settings, env.embedder, admin, "notion", complete
            )
        assert partial.removed == 0 and kept == 3
        assert final.removed == 2  # only a complete listing proves the others are gone


# ------------------------------------------------------------------ 4. knowledge graph


async def test_graph_and_who_knows_only_show_documents_the_caller_may_read() -> None:
    async with environment(demo_today=TODAY) as env:
        admin, member = env.principals[Role.ADMIN], env.principals[Role.MEMBER]
        source = Source([doc("secret", "Walrus merger negotiation strategy.", True)])
        async with env.database.session() as session:
            await sync_source(session, env.settings, env.embedder, admin, "notion", source)
            as_admin = await build_graph(session, env.collector, admin)
            as_member = await build_graph(session, env.collector, member)
            anonymous = await build_graph(session, env.collector, None)

        def doc_titles(graph) -> set[str]:
            return {i.title for i in graph.items.values() if i.kind == "document"}

        assert "Doc secret" in doc_titles(as_admin)
        assert "Doc secret" not in doc_titles(as_member)
        assert doc_titles(anonymous) == set()  # no principal: no documents at all
        evidence = " ".join(
            line for e in who_knows(as_member, "walrus merger") for line in e.evidence
        )
        assert "Doc secret" not in evidence


# ------------------------------------------------------------------ 8. partial project fetches


async def test_failed_or_capped_jira_fetch_keeps_indexed_issues() -> None:
    async with environment(demo_today=TODAY) as env:
        admin = env.principals[Role.ADMIN]
        async with env.database.session() as session:
            project = await session.scalar(select(Project).where(Project.key == "PHX"))
            snapshot = await env.collector.collect(project, today=TODAY)
            await index_project(
                session, env.settings, env.embedder, admin, env.hub, project, snapshot
            )

            def count():
                return session.scalar(
                    select(func.count()).select_from(Document).where(Document.source_type == "jira")
                )

            before = await count()
            # Jira failed in the snapshot: no issues, marked incomplete → nothing removed.
            snapshot.issues, snapshot.incomplete = [], {"jira"}
            result = await index_project(
                session, env.settings, env.embedder, admin, env.hub, project, snapshot
            )
            after = await count()
        jira = next(s for s in result.sources if s.source == "jira")
        assert before > 0 and after == before
        assert jira.removed == 0 and jira.complete is False


async def test_windowed_sources_are_never_pruned() -> None:
    from backend.app.domains.knowledge.indexing import WINDOWED_SOURCES

    assert {"email", "meetings"} <= WINDOWED_SOURCES


# ------------------------------------------------------------------ 9 / 17. external endpoints


def settings(**values) -> Settings:
    return Settings(
        **{"_env_file": None, "environment": "test", "jwt_secret_key": "k" * 40, **values}
    )


def test_endpoint_locality() -> None:
    assert endpoint_locality("http://localhost:11434") == "local"
    assert endpoint_locality("http://127.0.0.1:8000") == "local"
    assert endpoint_locality("http://ollama:11434") == "self_hosted"  # compose service name
    assert endpoint_locality("http://10.0.3.7:8000") == "self_hosted"
    assert endpoint_locality("http://vllm.ai.svc.cluster.local") == "self_hosted"
    assert endpoint_locality("https://api.openai.com/v1") == "external"
    assert endpoint_locality("https://generativelanguage.googleapis.com") == "external"
    assert endpoint_locality("https://8.8.8.8") == "external"


class Recorder:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def embed(self, texts):
        self.sent.extend(texts)
        return [[1.0] for _ in texts]

    async def close(self) -> None:
        return None


async def test_external_embedding_servers_only_receive_redacted_text() -> None:
    inner = Recorder()
    embedder = RedactingEmbedder(inner)
    await embedder.embed(["Key AKIAABCDEFGHIJKLMNOP, card 4111 1111 1111 1111, PAN ABCDE1234F"])
    sent = inner.sent[0]
    assert "AKIAABCDEFGHIJKLMNOP" not in sent and "4111 1111 1111 1111" not in sent
    assert "[REDACTED" in sent

    external = settings(
        embedding_provider="openai_compatible",
        embedding_model_name="text-embedding-3-small",
        embedding_base_url="https://api.openai.com",
    )
    local = external.model_copy(update={"embedding_base_url": "http://localhost:11434"})
    assert isinstance(build_embedder(external), RedactingEmbedder)
    assert not isinstance(build_embedder(local), RedactingEmbedder)
    always = local.model_copy(update={"embedding_redaction": "always"})
    assert isinstance(build_embedder(always), RedactingEmbedder)
    assert isinstance(build_embedder(settings()), HashingEmbedder)  # offline, nothing sent


def test_compliance_evidence_follows_the_configured_providers() -> None:
    local = _data_protection(
        settings(llm1_model_name="qwen2.5", llm_base_url="http://localhost:11434")
    )
    assert any("run on this host" in e for e in local.evidence)
    assert not any("external" in g for g in local.gaps)
    assert not any("no third-party" in e.lower() for e in local.evidence)

    cloud = _data_protection(
        settings(
            llm_provider="openai_compatible",
            llm1_model_name="gemini-2.0-flash",
            llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            llm_api_key=SecretStr("x"),
            embedding_provider="openai_compatible",
            embedding_model_name="text-embedding-004",
            embedding_redaction="never",
        )
    )
    assert cloud.status == "warn"
    assert any("external" in e for e in cloud.evidence)
    assert any("LLM_BASE_URL is an external service" in g for g in cloud.gaps)
    assert any("unredacted" in g for g in cloud.gaps)
