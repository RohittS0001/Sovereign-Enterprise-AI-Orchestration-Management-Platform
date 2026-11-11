"""Weighted RRF, identifier-aware keyword terms, exact-match promotion and platform indexing."""

from datetime import date

import pytest
from sqlalchemy import func, select

from backend.app.core.security.permissions import Permission, Role
from backend.app.db.models import Document, Project
from backend.app.domains.knowledge.indexing import IndexItem, index_project, roles_with
from backend.app.domains.knowledge.ranking import (
    exact_match_first,
    reciprocal_rank_fusion,
    search_terms,
)
from backend.app.domains.knowledge.service import SearchFilters
from evaluation.environment import environment

TODAY = date(2026, 11, 9)


def test_weighted_rrf_lets_one_retriever_count_more() -> None:
    keyword, semantic = ["a", "b", "c"], ["c", "b", "a"]
    equal = dict(reciprocal_rank_fusion([keyword, semantic], k=60))
    assert equal["a"] == pytest.approx(equal["c"])  # symmetric lists tie
    weighted = reciprocal_rank_fusion([keyword, semantic], k=5, weights=[1.0, 0.1])
    assert [key for key, _ in weighted] == ["a", "b", "c"]  # keyword order wins
    assert reciprocal_rank_fusion([keyword, semantic], weights=[1.0, 0.0])[0][0] == "a"
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([keyword], weights=[1.0, 1.0])


def test_fusion_combines_partial_rankings() -> None:
    # Each retriever finds one of the two relevant items first; fusion ranks both on top.
    keyword, semantic = ["kw-hit", "x", "y", "both"], ["sem-hit", "both", "z", "w"]
    top = [key for key, _ in reciprocal_rank_fusion([keyword, semantic], k=60)][:3]
    assert set(top) == {"kw-hit", "sem-hit", "both"}


def test_search_terms_keep_identifiers_and_fold_plurals() -> None:
    terms = search_terms(
        "PHX-7 timeouts, see #103 and arjun.m@acme.example on payments-api policies"
    )
    assert {"phx-7", "#103", "arjun.m@acme.example", "payments-api"} <= set(terms)
    assert "timeout" in terms and "policy" in terms and "status" not in search_terms("statuses")
    assert search_terms("class 2026") == ["class", "2026"]  # "ss" and numbers untouched


def test_exact_identifier_matches_come_first() -> None:
    ranked = [("c3", 0.3), ("c7", 0.2), ("c9", 0.1)]
    titles = {"c3": "PHX-3: checkout (blocked by PHX-7)", "c7": "PHX-7: vendor", "c9": "PHX-70"}
    promoted = [c for c, _ in exact_match_first("why is PHX-7 blocked", ranked, titles)]
    # The record itself first, then a title that mentions it; "PHX-70" is not PHX-7.
    assert promoted == ["c7", "c3", "c9"]
    assert exact_match_first("why is it blocked", ranked, titles) == ranked


def test_roles_follow_permissions() -> None:
    assert roles_with(Permission.EMAIL_READ) == ["member", "project_manager", "team_lead"]
    assert "member" not in roles_with(Permission.PEOPLE_READ)
    assert roles_with(Permission.ANALYTICS_READ) == ["auditor", "project_manager", "team_lead"]


async def test_indexing_is_incremental_access_aware_and_survives_outages(monkeypatch) -> None:
    from backend.app.domains.knowledge import indexing

    async with environment(demo_today=TODAY) as env:
        admin = env.principals[Role.ADMIN]
        async with env.database.session() as session:
            project = await session.scalar(select(Project).where(Project.key == "PHX"))
            snapshot = await env.collector.collect(project, today=TODAY)
            first = await index_project(
                session, env.settings, env.embedder, admin, env.hub, project, snapshot
            )
        sources = {s.source for s in first.sources if s.added}
        assert {"jira", "github", "slack", "email", "incidents", "keka", "meetings"} <= sources

        async def search(role, query, **filters):
            async with env.database.session() as session:
                return await env.knowledge(session).search(
                    env.principals[role],
                    query,
                    top_k=5,
                    filters=SearchFilters(**filters) if filters else None,
                )

        # Filters narrow before ranking; roles without the permission see nothing.
        emails = await search(Role.PROJECT_MANAGER, "vendor", sources=("email",))
        assert emails and {h.source for h in emails} == {"email"}
        assert not await search(Role.AUDITOR, "vendor", sources=("email",))
        assert not [h for h in await search(Role.MEMBER, "Arjun M leave") if h.source == "keka"]
        recent = await search(Role.PROJECT_MANAGER, "PHX", project="PHX")
        assert recent and all(h.project_key == "PHX" for h in recent)

        # A record that changed is re-embedded; one that vanished is removed …
        original = indexing.jira_items

        def changed(snap, facts):
            items = original(snap, facts)
            items[0] = IndexItem(**{**vars(items[0]), "text": items[0].text + "\nEdited."})
            return items[:-1]

        monkeypatch.setattr(indexing, "jira_items", changed)
        async with env.database.session() as session:
            second = await index_project(
                session, env.settings, env.embedder, admin, env.hub, project, snapshot
            )
        jira = next(s for s in second.sources if s.source == "jira")
        assert (jira.updated, jira.removed, jira.added) == (1, 1, 0)

        # … but a source that fails to load keeps its documents.
        async def broken(*_):
            raise RuntimeError("Slack is down")

        monkeypatch.setattr(indexing, "chat_items", broken)
        async with env.database.session() as session:
            before = await session.scalar(
                select(func.count()).select_from(Document).where(Document.source_type == "slack")
            )
            third = await index_project(
                session, env.settings, env.embedder, admin, env.hub, project, snapshot
            )
            after = await session.scalar(
                select(func.count()).select_from(Document).where(Document.source_type == "slack")
            )
        assert before == after > 0
        assert any(s.source == "chat" and s.error == "Slack is down" for s in third.sources)
