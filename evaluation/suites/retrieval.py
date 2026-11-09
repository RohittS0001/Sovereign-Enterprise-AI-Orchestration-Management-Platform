"""Retrieval quality and access control of the knowledge base."""

import math

from backend.app.core.security.permissions import Role
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment


def _relevant(hit: dict, target: dict) -> bool:
    return (
        hit["title"] == target["title"]
        and target["section"].lower() in (hit["section"] or "").lower()
    )


async def run(*, real_embeddings: bool = False) -> SuiteResult:
    data = load_dataset("retrieval")
    k = data["k"]
    result = SuiteResult("retrieval")
    hits_at_1 = recalls = reciprocal = ndcg = 0.0
    graded = leaks = leak_checks = 0

    with Timer() as timer:
        async with environment(real_embeddings=real_embeddings) as env:
            for case in data["queries"]:
                principal = env.principals[Role(case["role"])]
                async with env.database.session() as session:
                    found = [
                        h.as_dict()
                        for h in await env.knowledge(session).search(
                            principal, case["query"], top_k=5
                        )
                    ]

                if "forbidden" in case:
                    leak_checks += 1
                    leaked = [h["title"] for h in found if h["title"] in case["forbidden"]]
                    leaks += bool(leaked)
                    result.cases.append(CaseResult(case["id"], not leaked, {"leaked": leaked}))
                    continue

                graded += 1
                target = case["relevant"][0]
                rank = next((i + 1 for i, h in enumerate(found) if _relevant(h, target)), None)
                hits_at_1 += rank == 1
                recalls += rank is not None and rank <= k
                reciprocal += 1 / rank if rank else 0
                ndcg += 1 / math.log2(rank + 1) if rank else 0
                result.cases.append(
                    CaseResult(
                        case["id"],
                        rank is not None and rank <= k,
                        {"rank": rank, "top": [h["citation"] for h in found[:3]]},
                    )
                )

    t = data["thresholds"]
    result.metrics = [
        Metric("hit_at_1", ratio(hits_at_1, graded), t["hit_at_1"], description="Best passage ranked first"),
        Metric("recall_at_k", ratio(recalls, graded), t["recall_at_k"], description=f"Relevant passage in top {k}"),
        Metric("mrr", ratio(reciprocal, graded), t["mrr"], description="Mean reciprocal rank"),
        Metric("ndcg_at_5", ratio(ndcg, graded), None, description="Binary-relevance nDCG@5"),
        Metric("leak_rate", ratio(leaks, leak_checks), t["leak_rate"], higher_is_better=False,
               description="Restricted documents returned to unauthorized roles"),
    ]  # fmt: skip
    result.seconds = timer.seconds
    return result
