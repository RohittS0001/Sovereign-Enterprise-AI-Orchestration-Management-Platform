"""Metric implementations shared by the suites: the standard formulas, written out.

Retrieval (ranked list of results, each judged relevant or not / with a graded gain):

- **Hit rate@k**: share of queries with at least one relevant result in the top k.
- **Precision@k**: relevant results in the top k / k.
- **Recall@k**: relevant results found in the top k / all relevant results.
- **MRR**: mean of 1 / rank of the first relevant result (0 if none).
- **nDCG@k**: DCG = Σ gainᵢ / log₂(i + 1), normalized by the ideal ordering's DCG. Uses graded
  gains (2 = the exact section, 1 = the right document) so near-misses earn partial credit.
- **Context precision@k** (Ragas definition): average of precision@i over the positions i
  that hold a relevant chunk, i.e. rank-aware: relevant chunks ranked high score more.

Generation (offline versions are deterministic proxies; the ``judges`` module adds the
LLM-as-judge versions used by Ragas/DeepEval when a model is configured):

- **Faithfulness**: share of answer claims supported by the retrieved context.
- **Answer relevance**: similarity between the question and the answer.
- **Semantic similarity**: embedding cosine between the answer and a reference answer.
- **Factual correctness**: precision / recall / F1 of the answer's facts against the
  reference facts.

Agents: tool precision / recall / F1, trajectory match (exact, in order, any order) and
argument accuracy. Every suite can attach a **bootstrap 95% confidence interval** because
the datasets are small, so a point estimate alone would overstate certainty.
"""

import math
import random
import re
import statistics
from collections.abc import Callable, Sequence

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "is",
    "are",
    "be",
    "by",
    "with",
    "it",
    "that",
    "this",
    "as",
    "at",
    "from",
    "if",
    "we",
    "our",
    "you",
    "your",
    "what",
    "who",
    "when",
    "how",
    "which",
    "can",
    "should",
    "does",
    "do",
    "must",
    "any",
    "all",
    "not",
    "no",
}
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


# ------------------------------------------------------------------ retrieval


def hit_at_k(relevance: Sequence[float], k: int) -> float:
    return 1.0 if any(r > 0 for r in relevance[:k]) else 0.0


def precision_at_k(relevance: Sequence[float], k: int) -> float:
    return sum(1 for r in relevance[:k] if r > 0) / k if k else 0.0


def recall_at_k(relevance: Sequence[float], k: int, total_relevant: int) -> float:
    if total_relevant <= 0:
        return 1.0
    return min(1.0, sum(1 for r in relevance[:k] if r > 0) / total_relevant)


def reciprocal_rank(relevance: Sequence[float]) -> float:
    return next((1 / (i + 1) for i, r in enumerate(relevance) if r > 0), 0.0)


def ndcg_at_k(gains: Sequence[float], k: int, ideal_gains: Sequence[float] | None = None) -> float:
    def dcg(values: Sequence[float]) -> float:
        return sum(g / math.log2(i + 2) for i, g in enumerate(values[:k]))

    ideal = dcg(sorted(ideal_gains if ideal_gains is not None else gains, reverse=True))
    return dcg(gains) / ideal if ideal > 0 else 0.0


def context_precision_at_k(relevance: Sequence[float], k: int) -> float:
    """Ragas context precision: mean precision@i over relevant positions i ≤ k."""
    hits, total = 0, 0.0
    for i, r in enumerate(relevance[:k], start=1):
        if r > 0:
            hits += 1
            total += hits / i
    return total / hits if hits else 0.0


# ------------------------------------------------------------------ text helpers


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9%]+", text.lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in _STOP}


def claims(text: str) -> list[str]:
    """Split an answer into claim-sized sentences (citations and markup removed)."""
    clean = re.sub(r"\[S\d+\]|\*\*|`", "", text)
    return [s.strip(" -•\t") for s in _SENTENCE.split(clean) if len(s.strip()) > 12]


def support(claim: str, contexts: Sequence[str]) -> float:
    """Best share of the claim's content words found in a single context sentence."""
    words = content_words(claim)
    if not words:
        return 1.0
    best = 0.0
    for context in contexts:
        for sentence in _SENTENCE.split(context) or [context]:
            best = max(best, len(words & content_words(sentence)) / len(words))
    return best


def faithfulness(answer: str, contexts: Sequence[str], threshold: float = 0.7) -> float:
    """Supported claims / all claims (offline proxy for Ragas faithfulness)."""
    parts = claims(answer)
    if not parts:
        return 1.0
    return sum(support(c, contexts) >= threshold for c in parts) / len(parts)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def factual_correctness(
    answer: str, reference_facts: Sequence[str], reference: str = ""
) -> dict[str, float]:
    """Recall: reference facts present in the answer. Precision: answer claims supported by
    the reference answer and facts. F1 combines them (DeepEval/Ragas "factual correctness")."""
    text = answer.lower()
    recall = (
        sum(f.lower() in text for f in reference_facts) / len(reference_facts)
        if reference_facts
        else 1.0
    )
    parts = claims(answer)
    grounding = [reference, *reference_facts]
    precision = sum(support(c, grounding) >= 0.5 for c in parts) / len(parts) if parts else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def abstained(answer: str) -> bool:
    """Did the answer decline instead of guessing (negative rejection)?"""
    return bool(
        re.search(
            r"(could not find|couldn't find|not (in|found in) the (documents|data)|don't have|"
            r"do not have|no information|not able to find|unable to find|isn't mentioned|"
            r"not mentioned)",
            answer,
            re.I,
        )
    )


# ------------------------------------------------------------------ agents


def tool_scores(expected: Sequence[str], used: Sequence[str]) -> dict[str, float]:
    expected_set, used_set = set(expected), set(used)
    hit = len(expected_set & used_set)
    precision = hit / len(used_set) if used_set else (1.0 if not expected_set else 0.0)
    recall = hit / len(expected_set) if expected_set else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def trajectory_match(expected: Sequence[str], used: Sequence[str]) -> dict[str, bool]:
    """Exact sequence; expected as an ordered subsequence; expected as a subset (any order)."""
    iterator = iter(used)
    in_order = all(step in iterator for step in expected)
    return {
        "exact": list(expected) == list(used),
        "in_order": in_order,
        "any_order": set(expected) <= set(used),
    }


def argument_accuracy(expected: dict, actual: dict) -> float:
    """Share of expected argument values the agent passed (case-insensitive for strings)."""
    if not expected:
        return 1.0

    def same(a: object, b: object) -> bool:
        return str(a).lower() == str(b).lower()

    return sum(k in actual and same(actual[k], v) for k, v in expected.items()) / len(expected)


# ------------------------------------------------------------------ statistics


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic: Callable = statistics.fmean,
    resamples: int = 1000,
    seed: int = 7,
) -> tuple[float, float]:
    """95% percentile-bootstrap interval of a per-case statistic (deterministic seed)."""
    if not values:
        return (0.0, 0.0)
    if len(set(values)) == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    n = len(values)
    samples = sorted(
        statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    return (
        round(samples[int(0.025 * resamples)], 4),
        round(samples[int(0.975 * resamples) - 1], 4),
    )


def mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 1.0
