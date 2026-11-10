"""Metric formulas checked against hand-computed values."""

import math

import pytest

from evaluation import metrics as M


def test_retrieval_metrics_on_a_hand_worked_ranking() -> None:
    # Ranking: miss, hit, miss, hit (2 relevant overall, k = 4).
    relevance = [0, 1, 0, 1]
    assert M.hit_at_k(relevance, 1) == 0 and M.hit_at_k(relevance, 2) == 1
    assert M.precision_at_k(relevance, 4) == 0.5
    assert M.recall_at_k(relevance, 2, total_relevant=2) == 0.5
    assert M.reciprocal_rank(relevance) == 0.5
    # Ragas context precision: mean of precision@2 = 1/2 and precision@4 = 2/4.
    assert M.context_precision_at_k(relevance, 4) == pytest.approx(0.5)
    assert M.context_precision_at_k([1, 1, 0, 0], 4) == 1.0
    assert M.context_precision_at_k([0, 0, 0], 3) == 0.0


def test_graded_ndcg() -> None:
    gains = [1, 2, 0]
    dcg = 1 / math.log2(2) + 2 / math.log2(3)
    ideal = 2 / math.log2(2) + 1 / math.log2(3)
    assert M.ndcg_at_k(gains, 3) == pytest.approx(dcg / ideal)
    assert M.ndcg_at_k([2, 1], 2) == 1.0
    assert M.ndcg_at_k([0, 0], 2) == 0.0
    # Missing the second relevant passage is penalized against the ideal list.
    assert M.ndcg_at_k([2, 0], 2, ideal_gains=[2, 2]) < 1


def test_faithfulness_counts_supported_claims() -> None:
    context = ["Each SLA breach earns a service credit of 5% of the monthly fee, capped at 30%."]
    grounded = "Each SLA breach earns a 5% service credit of the monthly fee [S1]."
    invented = grounded + " The vendor also pays a cash penalty of one million rupees."
    assert M.faithfulness(grounded, context) == 1.0
    assert M.faithfulness(invented, context) == 0.5
    assert M.faithfulness("", context) == 1.0


def test_factual_correctness_precision_recall_f1() -> None:
    reference = "Teams commit to at most 85% of their average velocity, minus planned leave."
    full = M.factual_correctness(
        "Commit at most 85% of average velocity minus planned leave.",
        ["85%", "planned leave"],
        reference,
    )
    partial = M.factual_correctness(
        "Commit 85% of velocity. Also order pizza every Friday night.",
        ["85%", "planned leave"],
        reference,
    )
    assert full == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert partial["recall"] == 0.5 and partial["precision"] == 0.5
    assert partial["f1"] == pytest.approx(0.5)


def test_abstention_detection() -> None:
    assert M.abstained("I could not find this in the documents.")
    assert M.abstained("The handbook isn't mentioned in the knowledge base.")
    assert not M.abstained("Parental leave is 26 weeks.")


def test_agent_tool_and_trajectory_scores() -> None:
    scores = M.tool_scores(["jira.get_issue"], ["jira.get_issue", "jira.search_issues"])
    assert scores == {"precision": 0.5, "recall": 1.0, "f1": pytest.approx(2 / 3)}
    match = M.trajectory_match(["a", "c"], ["a", "b", "c"])
    assert match == {"exact": False, "in_order": True, "any_order": True}
    assert M.trajectory_match(["c", "a"], ["a", "b", "c"])["in_order"] is False
    assert M.argument_accuracy({"project_key": "PHX", "n": 3}, {"project_key": "phx"}) == 0.5


def test_percentiles_and_bootstrap_interval() -> None:
    assert M.percentile([1, 2, 3, 4], 50) == 2.5
    assert M.percentile([5], 95) == 5
    low, high = M.bootstrap_ci([1.0] * 8 + [0.0] * 2)
    assert 0.4 <= low <= 0.8 <= high <= 1.0
    assert M.bootstrap_ci([1.0, 1.0]) == (1.0, 1.0)
    assert M.bootstrap_ci([0.0, 1.0] * 5) == M.bootstrap_ci([0.0, 1.0] * 5)  # deterministic
