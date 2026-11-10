"""Org knowledge graph: expertise ranking, impact of absence, bus factor."""

from collections import Counter

from backend.app.domains.graph.service import (
    Item,
    OrgGraph,
    bus_factor,
    impact_of_absence,
    topics,
    who_knows,
)


def graph() -> OrgGraph:
    g = OrgGraph()
    add = lambda key, item: g.items.__setitem__(key, item)  # noqa: E731
    add("issue:R-1", Item("issue", "R-1", "Refunds webhook retries", done=True, people={"Ana"}))
    add("issue:R-2", Item("issue", "R-2", "Refunds ledger export", people={"Ana"}))
    add("issue:R-3", Item("issue", "R-3", "Refund reconciliation report", people={"Bo"},
                          blocked_by=["R-2"]))  # fmt: skip
    add("pr:pay#1", Item("pull_request", "pay#1", "Refunds retry with backoff", repository="acme/payments",
                         done=True, people={"Ana"}))  # fmt: skip
    add("doc:1", Item("document", "1", "Refunds runbook", people={"Cy"}))
    add("issue:C-1", Item("issue", "C-1", "Checkout button colour", people={"Bo"}))
    g.repo_authors["acme/payments"] = Counter({"Ana": 4, "Bo": 1})
    g.repo_authors["acme/web"] = Counter({"Bo": 2, "Cy": 2, "Ana": 1})
    return g


def test_topics_drop_stopwords_and_fold_plurals() -> None:
    assert {"refund", "refunds", "webhook", "webhooks"} <= topics("Fix the refunds webhooks")
    assert "the" not in topics("the fix")


def test_who_knows_prefers_delivered_work() -> None:
    experts = who_knows(graph(), "refunds")

    assert experts[0].person == "Ana" and experts[0].score > experts[1].score
    assert any("(done)" in line for line in experts[0].evidence)
    assert who_knows(graph(), "the and") == []


def test_impact_of_absence_finds_sole_work_blockers_and_backups() -> None:
    impact = impact_of_absence(graph(), "Ana")

    assert impact.sole_open_work == ["R-2: Refunds ledger export"]
    assert impact.blocked_behind == ["R-3"]
    assert impact.concentrated_repositories == ["acme/payments"]  # 4 of 5 PRs
    assert impact.severity == "high"
    assert "Ana" not in [b.person for b in impact.suggested_backups]
    assert impact_of_absence(graph(), "Nobody").severity == "low"


def test_bus_factor() -> None:
    # acme/web: Bo 2 + Cy 2 = 4 of 5 PRs (80%) → two people
    assert bus_factor(graph()) == {"acme/payments": 1, "acme/web": 2}
