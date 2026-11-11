"""Retrieval quality, access control and an ablation of the hybrid retriever.

Each graded query lists its relevant passages (document title + section). A result earns
gain 2 when it is the exact section and 1 when it is another part of a relevant document,
so nDCG rewards near-misses partially. Metrics (see ``evaluation.metrics``): Hit rate@1,
Hit rate@k, Precision@k, Recall@k, MRR, nDCG@k, context precision@k (rank-aware, Ragas
definition), p50/p95 search latency and the leak rate for restricted documents. The same
queries are re-run with keyword-only (BM25) and semantic-only retrieval to show what the
hybrid (RRF) fusion adds. Every headline metric carries a bootstrap 95% CI.
"""

import time

from backend.app.core.security.permissions import Role
from evaluation import metrics as M
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment

STRATEGIES = ("hybrid", "keyword", "semantic")
# RRF settings swept for the report: (k, semantic weight), keyword weight fixed at 1.
FUSION_GRID = ((5, 0.1), (5, 0.5), (60, 0.1), (60, 1.0))


def _gain(hit: dict, relevant: list[dict]) -> float:
    for target in relevant:
        if hit["title"] == target["title"]:
            exact = target["section"].lower() in (hit["section"] or "").lower()
            return 2.0 if exact else 1.0
    return 0.0


async def run(*, real_embeddings: bool = False) -> SuiteResult:
    data = load_dataset("retrieval")
    k = data["k"]
    result = SuiteResult("retrieval")
    per: dict[str, dict[str, list[float]]] = {s: {} for s in STRATEGIES}
    latencies: list[float] = []
    leaks = leak_checks = 0

    def record(strategy: str, name: str, value: float) -> None:
        per[strategy].setdefault(name, []).append(value)

    with Timer() as timer:
        async with environment(real_embeddings=real_embeddings) as env:
            for case in data["queries"]:
                principal = env.principals[Role(case["role"])]
                if "forbidden" in case:
                    async with env.database.session() as session:
                        found = await env.knowledge(session).search(
                            principal, case["query"], top_k=5
                        )
                    leak_checks += 1
                    leaked = [h.title for h in found if h.title in case["forbidden"]]
                    leaks += bool(leaked)
                    result.cases.append(CaseResult(case["id"], not leaked, {"leaked": leaked}))
                    continue

                for strategy in STRATEGIES:
                    started = time.perf_counter()
                    async with env.database.session() as session:
                        found = [
                            h.as_dict()
                            for h in await env.knowledge(session).search(
                                principal, case["query"], top_k=5, strategy=strategy
                            )
                        ]
                    if strategy == "hybrid":
                        latencies.append((time.perf_counter() - started) * 1000)
                    gains = [_gain(h, case["relevant"]) for h in found]
                    exact = [1.0 if g == 2 else 0.0 for g in gains]
                    ideal = [2.0] * len(case["relevant"]) + [1.0] * 4
                    record(strategy, "hit_at_1", M.hit_at_k(exact, 1))
                    record(strategy, "hit_at_k", M.hit_at_k(exact, k))
                    record(strategy, "precision_at_k", M.precision_at_k(gains, k))
                    record(strategy, "recall_at_k", M.recall_at_k(exact, k, len(case["relevant"])))
                    record(strategy, "mrr", M.reciprocal_rank(exact))
                    record(strategy, "ndcg_at_k", M.ndcg_at_k(gains, k, ideal))
                    record(strategy, "context_precision_at_k", M.context_precision_at_k(gains, k))
                    if strategy == "hybrid":
                        rank = next((i + 1 for i, e in enumerate(exact) if e), None)
                        result.cases.append(
                            CaseResult(
                                case["id"],
                                rank is not None and rank <= k,
                                {"rank": rank, "top": [h["citation"] for h in found[:3]]},
                            )
                        )

        # Sweep the fusion settings on the same queries (informational, shows the tuning).
        grid: dict[tuple[int, float], list[float]] = {}
        async with environment(real_embeddings=real_embeddings) as env:
            for rrf_k, weight in FUSION_GRID:
                for case in data["queries"]:
                    if "forbidden" in case:
                        continue
                    async with env.database.session() as session:
                        found = await env.knowledge(session).search(
                            env.principals[Role(case["role"])],
                            case["query"],
                            top_k=5,
                            weights=(1.0, weight),
                            rrf_k=rrf_k,
                        )
                    exact = [
                        1.0 if _gain(h.as_dict(), case["relevant"]) == 2 else 0.0 for h in found
                    ]
                    grid.setdefault((rrf_k, weight), []).append(M.reciprocal_rank(exact))

    t = data["thresholds"]
    hybrid = per["hybrid"]
    best_single = max(M.mean(per["keyword"].get("mrr", [])), M.mean(per["semantic"].get("mrr", [])))

    def metric(name: str, threshold: float | None, description: str) -> Metric:
        values = hybrid.get(name, [])
        return Metric(
            name, M.mean(values), threshold, description=description, ci=M.bootstrap_ci(values)
        )

    result.metrics = [
        metric("hit_at_1", t["hit_at_1"], "Exact passage ranked first"),
        metric("hit_at_k", t["hit_at_k"], f"Exact passage in the top {k} (hit rate)"),
        metric("precision_at_k", t.get("precision_at_k"), f"Relevant results among the top {k}"),
        metric("recall_at_k", t.get("recall_at_k"), f"Relevant passages found in the top {k}"),
        metric("mrr", t["mrr"], "Mean reciprocal rank of the exact passage"),
        metric("ndcg_at_k", t.get("ndcg_at_k"), f"Graded nDCG@{k} (section 2, document 1)"),
        metric(
            "context_precision_at_k",
            t.get("context_precision_at_k"),
            "Rank-aware context precision (Ragas)",
        ),
        Metric(
            "latency_p50_ms",
            round(M.percentile(latencies, 50), 1),
            None,
            higher_is_better=False,
            description="Search latency, median",
        ),
        Metric(
            "latency_p95_ms",
            round(M.percentile(latencies, 95), 1),
            t.get("latency_p95_ms"),
            higher_is_better=False,
            description="Search latency, 95th percentile",
        ),
        Metric(
            "leak_rate",
            ratio(leaks, leak_checks),
            t["leak_rate"],
            higher_is_better=False,
            description="Restricted documents returned to unauthorized roles",
        ),
    ]
    # Fusion must never do worse than the best single retriever on these queries.
    result.metrics.append(
        Metric(
            "fusion_gain_mrr",
            round(M.mean(hybrid.get("mrr", [])) - best_single, 4),
            t.get("fusion_gain_mrr"),
            description="Hybrid MRR minus the best single retriever's MRR",
        )
    )
    for (rrf_k, weight), values in grid.items():
        result.metrics.append(
            Metric(
                f"rrf_k{rrf_k}_wsem{weight:g}_mrr",
                M.mean(values),
                None,
                description="Fusion sweep (keyword weight 1)",
            )
        )
    # Ablation: what each retriever achieves alone (informational, no thresholds).
    for strategy in ("keyword", "semantic"):
        for name in ("hit_at_1", "mrr", "ndcg_at_k"):
            result.metrics.append(
                Metric(
                    f"{strategy}_only_{name}",
                    M.mean(per[strategy].get(name, [])),
                    None,
                    description=f"Ablation: {strategy} retrieval alone",
                )
            )
    result.seconds = timer.seconds
    return result
