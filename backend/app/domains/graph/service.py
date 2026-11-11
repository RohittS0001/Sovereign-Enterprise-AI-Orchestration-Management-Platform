"""Org knowledge graph: people ↔ issues ↔ pull requests ↔ repositories ↔ documents ↔ topics.

Built on demand from every active project's snapshot (Jira issues and links, GitHub pull
requests) and the knowledge base (document owners). Answers:

- **who knows X?** — people ranked by delivered work (done issues, merged PRs) and
  documents whose text matches the topic, with the evidence;
- **what breaks if P leaves?** — open work only P holds, repositories where P wrote most
  of the changes (bus factor), work blocked behind P's issues, and suggested backups.

Pure Python, no graph database: the graph is small (one organization's active projects).
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security.permissions import Principal
from backend.app.db.models import Document, Project, User
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.knowledge.service import access_filter

_WORD = re.compile(r"[a-z][a-z0-9]{2,}")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "when",
    "that",
    "this",
    "add",
    "fix",
    "use",
    "phx",
    "new",
    "update",
    "issue",
    "task",
    "story",
    "bug",
    "support",
    "make",
    "page",
}
CONCENTRATION = 0.6  # share of a repository's PRs that makes someone a single point of failure


def topics(text: str) -> set[str]:
    words = {w for w in _WORD.findall(text.lower()) if w not in _STOP}
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 4}  # crude plural folding


@dataclass
class Item:
    kind: str  # issue | pull_request | document
    id: str
    title: str
    project: str | None = None
    repository: str | None = None
    done: bool = False
    people: set[str] = field(default_factory=set)
    blocked_by: list[str] = field(default_factory=list)

    @property
    def words(self) -> set[str]:
        return topics(f"{self.title} {self.repository or ''}")


@dataclass
class OrgGraph:
    items: dict[str, Item] = field(default_factory=dict)
    repo_authors: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    @property
    def people(self) -> set[str]:
        return {p for item in self.items.values() for p in item.people}

    def edges(self) -> int:
        return sum(
            len(i.people) + len(i.blocked_by) + (1 if i.repository else 0)
            for i in self.items.values()
        )


class Expert(BaseModel):
    person: str
    score: float
    evidence: list[str] = Field(default_factory=list)


class Absence(BaseModel):
    person: str
    sole_open_work: list[str] = Field(default_factory=list)
    blocked_behind: list[str] = Field(default_factory=list)
    concentrated_repositories: list[str] = Field(default_factory=list)
    documents_owned: list[str] = Field(default_factory=list)
    suggested_backups: list[Expert] = Field(default_factory=list)
    severity: str = "low"


class GraphSummary(BaseModel):
    people: int
    items: int
    edges: int
    bus_factor: dict[str, int]


WEIGHTS = {
    ("issue", False): 1.0,
    ("issue", True): 1.5,
    ("pull_request", False): 1.5,
    ("pull_request", True): 2.5,
    ("document", False): 1.0,
    ("document", True): 1.0,
}


async def build_graph(
    session: AsyncSession, collector: SnapshotCollector, principal: Principal | None = None
) -> OrgGraph:
    """The graph as ``principal`` may see it: documents go through the same access filter as
    knowledge search (``access_filter``) before their titles or owners become graph items.
    Without a principal no documents are included (fail closed)."""
    graph = OrgGraph()
    projects = await session.scalars(select(Project).where(Project.is_active.is_(True)))
    for project in projects:
        snapshot = await collector.collect(project)
        # One person, many identities: GitHub logins resolve to the member's name.
        alias = {m.github_login.lower(): m.name for m in snapshot.members if m.github_login}
        for issue in snapshot.issues:
            graph.items[f"issue:{issue.key}"] = Item(
                "issue",
                issue.key,
                issue.summary,
                project.key,
                done=issue.category == "done",
                people={issue.assignee} if issue.assignee else set(),
                blocked_by=list(issue.blocked_by),
            )
        for pull in snapshot.pulls:
            author = alias.get((pull.author or "").lower(), pull.author or "unknown")
            graph.items[f"pr:{pull.repository}#{pull.number}"] = Item(
                "pull_request",
                f"{pull.repository}#{pull.number}",
                pull.title,
                project.key,
                repository=pull.repository,
                done=pull.state == "merged",
                people={author},
            )
            graph.repo_authors[pull.repository][author] += 1
    if principal is None:
        return graph
    owners = {u.id: u.full_name for u in await session.scalars(select(User))}
    for doc in await session.scalars(select(Document).where(access_filter(principal))):
        if doc.owner_id in owners:
            graph.items[f"doc:{doc.id}"] = Item(
                "document", str(doc.id), doc.title, people={owners[doc.owner_id]}
            )
    return graph


def who_knows(graph: OrgGraph, query: str, limit: int = 5) -> list[Expert]:
    wanted = topics(query)
    if not wanted:
        return []
    scores: dict[str, float] = defaultdict(float)
    evidence: dict[str, list[str]] = defaultdict(list)
    for item in graph.items.values():
        overlap = len(wanted & item.words)
        if not overlap:
            continue
        for person in item.people:
            scores[person] += WEIGHTS[(item.kind, item.done)] * overlap
            evidence[person].append(
                f"{item.kind.replace('_', ' ')} {item.id}: {item.title}"
                + (" (done)" if item.done and item.kind != "document" else "")
            )
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [Expert(person=p, score=round(s, 1), evidence=evidence[p][:5]) for p, s in ranked]


def bus_factor(graph: OrgGraph) -> dict[str, int]:
    """Fewest authors who together wrote at least 80% of a repository's pull requests."""
    result = {}
    for repo, authors in graph.repo_authors.items():
        total, covered, count = sum(authors.values()), 0, 0
        for _, n in authors.most_common():
            covered, count = covered + n, count + 1
            if covered >= 0.8 * total:
                break
        result[repo] = count
    return result


def impact_of_absence(graph: OrgGraph, person: str) -> Absence:
    sole = [
        i for i in graph.items.values() if i.kind == "issue" and not i.done and i.people == {person}
    ]
    sole_keys = {i.id for i in sole}
    blocked = [
        i.id
        for i in graph.items.values()
        if not i.done and any(b in sole_keys for b in i.blocked_by)
    ]
    concentrated = [
        repo
        for repo, authors in graph.repo_authors.items()
        if sum(authors.values()) >= 3 and authors[person] / sum(authors.values()) >= CONCENTRATION
    ]
    docs = [i.title for i in graph.items.values() if i.kind == "document" and person in i.people]
    topic_text = " ".join(i.title for i in sole) + " " + " ".join(concentrated)
    backups = [e for e in who_knows(graph, topic_text, limit=6) if e.person != person][:3]
    severity = "high" if concentrated or len(blocked) >= 2 else "medium" if sole else "low"
    return Absence(
        person=person,
        sole_open_work=[f"{i.id}: {i.title}" for i in sole],
        blocked_behind=blocked,
        concentrated_repositories=concentrated,
        documents_owned=docs,
        suggested_backups=backups,
        severity=severity,
    )


def summary(graph: OrgGraph) -> GraphSummary:
    return GraphSummary(
        people=len(graph.people),
        items=len(graph.items),
        edges=graph.edges(),
        bus_factor=bus_factor(graph),
    )
