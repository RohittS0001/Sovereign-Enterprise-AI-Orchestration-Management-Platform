"""Ranking primitives: BM25, reciprocal rank fusion, per-document diversity."""

import math
from collections import Counter
from collections.abc import Hashable, Sequence

from backend.app.domains.knowledge.embeddings import tokenize


def bm25_scores(
    query: str, documents: Sequence[str], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """Okapi BM25 score of each document for the query (0 when no term matches)."""
    query_terms = set(tokenize(query))
    if not query_terms or not documents:
        return [0.0] * len(documents)

    tokenized = [tokenize(d) for d in documents]
    avg_length = sum(len(t) for t in tokenized) / len(tokenized) or 1.0
    df = Counter(term for tokens in tokenized for term in set(tokens) & query_terms)
    n = len(documents)

    scores = []
    for tokens in tokenized:
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            tf = frequencies.get(term, 0)
            if not tf:
                continue
            idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1)
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / avg_length))
        scores.append(score)
    return scores


def reciprocal_rank_fusion[K: Hashable](
    rankings: Sequence[Sequence[K]], *, k: int = 60
) -> list[tuple[K, float]]:
    """Fuse ranked lists: score = Σ 1 / (k + rank). Robust to incomparable score scales."""
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def cap_per_group[K, G](items: Sequence[K], group_of: dict[K, G], limit: int) -> list[K]:
    """Keep ranking order but at most ``limit`` items per group (e.g. per document)."""
    counts: Counter[G] = Counter()
    kept = []
    for item in items:
        group = group_of[item]
        if counts[group] < limit:
            counts[group] += 1
            kept.append(item)
    return kept
