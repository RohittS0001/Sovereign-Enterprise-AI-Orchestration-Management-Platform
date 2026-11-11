"""Ranking primitives: BM25, reciprocal rank fusion, per-document diversity."""

import math
import re
from collections import Counter
from collections.abc import Hashable, Sequence

from backend.app.domains.knowledge.embeddings import tokenize

# Identifiers people search for verbatim: issue keys (PHX-7), PR/ticket numbers (#103),
# e-mail addresses and dotted/underscored names (payments-api, ledger_service).
_IDENTIFIER = re.compile(
    r"[a-z][a-z0-9]*-\d+|#\d+|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|[a-z0-9]+(?:[._-][a-z0-9]+)+"
)


def _stem(word: str) -> str:
    """Light plural folding ("timeouts" → "timeout", "policies" → "policy"); no dictionary."""
    if len(word) > 4 and not word.isdigit():
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("s") and not word.endswith(("ss", "us", "is")):
            return word[:-1]
    return word


def search_terms(text: str) -> list[str]:
    """Words (plurals folded) plus whole identifiers, so "PHX-7" matches PHX-7, not any "7"."""
    lowered = text.lower()
    return [_stem(w) for w in tokenize(lowered)] + _IDENTIFIER.findall(lowered)


def bm25_scores(
    query: str, documents: Sequence[str], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """Okapi BM25 score of each document for the query (0 when no term matches)."""
    query_terms = set(search_terms(query))
    if not query_terms or not documents:
        return [0.0] * len(documents)

    tokenized = [search_terms(d) for d in documents]
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
    rankings: Sequence[Sequence[K]],
    *,
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[K, float]]:
    """Fuse ranked lists: score = Σ weightᵢ / (k + rankᵢ).

    Uses ranks only, so BM25 scores and cosine similarities (incomparable scales) fuse
    cleanly. A larger ``k`` flattens the difference between top and lower ranks; weights let
    one retriever count more (e.g. keyword search when the embedder is weak). Ties keep the
    order in which items were first seen, so results are deterministic.
    """
    weights = list(weights) if weights is not None else [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("one weight per ranking")
    scores: dict[K, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        if weight <= 0:
            continue
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
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


def exact_match_first[K](
    query: str, ranked: list[tuple[K, float]], titles: dict[K, str]
) -> list[tuple[K, float]]:
    """Promote results whose title names an identifier from the query ("PHX-7", "#103").

    Someone typing an exact key wants that record first: titles that *start* with the key
    (the record itself, "PHX-7: …") rank above titles that merely mention it ("PR #102:
    PHX-7 fallback"), which rank above the rest. Order is otherwise preserved (a stable
    sort), and queries without identifiers are untouched.
    """
    wanted = {i for i in _IDENTIFIER.findall(query.lower()) if any(ch.isdigit() for ch in i)}
    if not wanted:
        return ranked

    def tier(key: K) -> int:
        title = titles.get(key, "").lower()
        if any(re.match(rf"{re.escape(w)}(?![\w-])", title) for w in wanted):
            return 0
        if any(re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", title) for w in wanted):
            return 1
        return 2

    return sorted(ranked, key=lambda item: tier(item[0]))
