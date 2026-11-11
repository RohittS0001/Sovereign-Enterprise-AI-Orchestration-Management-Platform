"""Hybrid search over everything indexed from the connected platforms.

The demo project PHX is indexed from every source (Jira with history and comments, GitHub,
Slack, e-mail, incidents, Linear, meetings, Keka leave, finance, CRM) next to the policy
documents. Then:

- **hit_at_1 / hit_at_k / mrr** — questions whose answer lives in one specific system find
  that record (e.g. "are we telling leadership the real status" → the Slack message).
- **filter_precision** — with ``sources`` / ``project_key`` filters, every result matches.
- **leak_rate** — roles without the source's permission never receive it (auditors: no
  e-mail; members: no HR leave, finance or CRM). Filters run in SQL before ranking.
- **reindex_changes** — indexing again without changes adds/updates/removes nothing.
"""

from sqlalchemy import select

from backend.app.core.security.permissions import Role
from backend.app.db.models import Project
from backend.app.domains.knowledge.indexing import index_project
from backend.app.domains.knowledge.service import SearchFilters
from evaluation import metrics as M
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment


def _matches(hit, target: dict) -> bool:
    return hit.source == target["source"] and (
        target["contains"].lower() in f"{hit.title}\n{hit.text}".lower()
    )


async def run() -> SuiteResult:
    data = load_dataset("platform_search")
    t, k = data["thresholds"], data["k"]
    result = SuiteResult("platform_search")
    hits_1, hits_k, rr = [], [], []
    filter_ok = filter_total = leaks = 0

    with Timer() as timer:
        async with environment() as env:
            admin = env.principals[Role.ADMIN]
            async with env.database.session() as session:
                project = await session.scalar(select(Project).where(Project.key == "PHX"))
                snapshot = await env.collector.collect(project)
                first = await index_project(
                    session, env.settings, env.embedder, admin, env.hub, project, snapshot
                )
            async with env.database.session() as session:
                second = await index_project(
                    session, env.settings, env.embedder, admin, env.hub, project, snapshot
                )
            indexed = {s.source: s.added for s in first.sources}
            changes = sum(s.added + s.updated + s.removed for s in second.sources)
            result.cases.append(CaseResult("indexed-sources", len(indexed) >= 8, indexed))

            async def search(role: str, query: str, filters: SearchFilters | None = None):
                async with env.database.session() as session:
                    return await env.knowledge(session).search(
                        env.principals[Role(role)], query, top_k=5, filters=filters
                    )

            for case in data["queries"]:
                found = await search(case["role"], case["query"])
                relevance = [
                    1.0 if any(_matches(h, r) for r in case["relevant"]) else 0.0 for h in found
                ]
                hits_1.append(M.hit_at_k(relevance, 1))
                hits_k.append(M.hit_at_k(relevance, k))
                rr.append(M.reciprocal_rank(relevance))
                result.cases.append(
                    CaseResult(
                        case["id"],
                        M.hit_at_k(relevance, k) == 1,
                        {"top": [f"{h.source}: {h.title}" for h in found[:3]]},
                    )
                )

            for case in data["filtered"]:
                spec = case["filters"]
                filters = SearchFilters(
                    sources=tuple(spec.get("sources", [])), project=spec.get("project_key")
                )
                found = await search(case["role"], case["query"], filters)
                good = [
                    h
                    for h in found
                    if (not filters.sources or h.source in filters.sources)
                    and (not filters.project or h.project_key == filters.project)
                ]
                filter_ok += len(good)
                filter_total += len(found)
                result.cases.append(
                    CaseResult(
                        case["id"],
                        bool(found) and len(good) == len(found),
                        {"sources": [h.source for h in found]},
                    )
                )

            for case in data["leaks"]:
                found = await search(case["role"], case["query"])
                leaked = [
                    f"{h.source}: {h.title}" for h in found if h.source in case["forbidden_sources"]
                ]
                leaks += bool(leaked)
                result.cases.append(CaseResult(case["id"], not leaked, {"leaked": leaked}))

    result.metrics = [
        Metric("hit_at_1", M.mean(hits_1), t["hit_at_1"], ci=M.bootstrap_ci(hits_1)),
        Metric("hit_at_k", M.mean(hits_k), t["hit_at_k"], ci=M.bootstrap_ci(hits_k)),
        Metric("mrr", M.mean(rr), t["mrr"], ci=M.bootstrap_ci(rr)),
        Metric("filter_precision", ratio(filter_ok, filter_total), t["filter_precision"]),
        Metric(
            "leak_rate", ratio(leaks, len(data["leaks"])), t["leak_rate"], higher_is_better=False
        ),
        Metric(
            "reindex_changes",
            float(changes),
            t["reindex_changes"],
            higher_is_better=False,
            description="Documents touched by a second index run with no changes",
        ),
        Metric("sources_indexed", float(len(indexed)), None),
    ]
    result.seconds = timer.seconds
    return result
