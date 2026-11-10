"""Retrieval-augmented answering, scored with the RAG metric family used by Ragas/DeepEval.

Context (retriever side):
- **context_recall** — share of the facts a correct answer needs that are present in the
  top-k chunks.
- **context_precision_at_k** — rank-aware precision of the top-k chunks (relevant
  documents ranked first score more), the Ragas definition.

Answer (generator side), through the real agent pipeline (planner → ``knowledge.search`` via
the gateway → responder). Offline the responder is a deterministic *extractive* model that
quotes the best sentence it was shown, which measures the RAG plumbing (redaction, untrusted
wrapping, truncation, citation mapping) without a model:
- **faithfulness** — share of answer claims supported by the context the responder saw.
- **answer_relevance** — embedding similarity between the question and the answer.
- **semantic_similarity** — embedding similarity between the answer and a reference answer.
- **factual_correctness_f1** — reference facts recovered (recall) and answer claims grounded
  in the reference (precision), combined as F1.
- **citation_validity** — every ``[S#]`` points at a successful tool call.

With ``--judge`` and a configured model, the LLM-as-judge versions (``evaluation.judges``)
are added as ``judge_*`` metrics. Finally **sync_leak_rate**: documents synced as restricted
must never reach a member. Headline metrics carry bootstrap 95% CIs.
"""

import json
import re
from typing import Any

from backend.app.core.security.permissions import Role
from backend.app.domains.agent.service import AgentService
from backend.app.domains.knowledge.sync import sync_source
from backend.app.llm.base import AgentModels, ChatMessage
from backend.app.llm.factory import build_agent_models
from backend.app.llm.fake import FakeLLM
from evaluation import metrics as M
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment

_CITE = re.compile(r"\[S(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_MARKUP = re.compile(r"<[^>]+>|[{}\[\]\"]")
_TEXT_FIELD = re.compile(r'"text":\s*"((?:[^"\\]|\\.)*)"')


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def extractive_responder(query: str):
    """A responder that answers with the observation sentence overlapping the query most."""

    def reply(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
        prompt = messages[-1].content
        blocks = re.findall(r"\[S(\d+)\][^\n]*\n(.*?)(?=\n\[S\d+\]|\Z)", prompt, re.DOTALL)
        best, best_score, best_step = "", -1, "1"
        asked = _words(query)
        for step, block in blocks:
            # Read the passages ("text" fields), not the result metadata around them.
            passages = [json.loads(f'"{m}"') for m in _TEXT_FIELD.findall(block)] or [block]
            for sentence in _SENTENCE.split(_MARKUP.sub(" ", " ".join(passages))):
                sentence = " ".join(sentence.split())
                words = _words(sentence)
                # The observation echoes the tool arguments ("query : ..."); quoting the
                # question back is not an answer, so skip sentences with nothing new in them.
                if sentence.lower().startswith("query") or not words - asked:
                    continue
                score = len(asked & words)
                if 20 < len(sentence) < 400 and score > best_score:
                    best, best_score, best_step = sentence, score, step
        return f"{best} [S{best_step}]" if best else "I could not find this in the documents."

    return reply


def _planner(query: str) -> FakeLLM:
    return FakeLLM([
        {"thought": "", "done": False,
         "steps": [{"tool": "knowledge.search", "arguments": {"query": query}, "purpose": "rag"}]},
        {"thought": "enough", "done": True, "steps": []},
    ])  # fmt: skip


async def run(*, judge: bool = False) -> SuiteResult:
    data = load_dataset("rag")
    result = SuiteResult("rag")
    k, t = data["k"], data["thresholds"]
    scores: dict[str, list[float]] = {}
    valid = cited = 0

    def add(name: str, value: float) -> None:
        scores.setdefault(name, []).append(value)

    with Timer() as timer:
        async with environment() as env:
            judge_model = None
            if judge:
                models = build_agent_models(env.settings)
                judge_model = models.reporting if models else None
                if judge_model is None:
                    result.cases.append(CaseResult("judge-skipped", True, {"reason": "no model"}))

            for case in data["questions"]:
                principal = env.principals[Role(case["role"])]
                async with env.database.session() as session:
                    hits = await env.knowledge(session).search(principal, case["query"], top_k=k)
                context = " ".join(h.text for h in hits).lower()
                found = [f for f in case["facts"] if f.lower() in context]
                relevance = [1.0 if h.title in case["relevant_titles"] else 0.0 for h in hits]
                add("context_recall", len(found) / len(case["facts"]))
                add("context_precision_at_k", M.context_precision_at_k(relevance, k))

                reporter = FakeLLM(extractive_responder(case["query"]))
                async with env.database.session() as session:
                    run_ = await AgentService(
                        session, env.settings, AgentModels(_planner(case["query"]), reporter),
                        env.registry,
                    ).run(principal, case["query"])  # fmt: skip
                answer = run_.answer or ""
                observed = reporter.calls[0]["messages"][-1].content if reporter.calls else ""
                observed_text = " ".join(_MARKUP.sub(" ", observed).split())
                citations = _CITE.findall(answer)
                ok_steps = {
                    s["step_id"].lstrip("S") for s in run_.steps if s["status"] == "success"
                }
                is_valid = bool(citations) and all(c in ok_steps for c in citations)
                body = _CITE.sub("", answer).strip()
                valid += is_valid
                cited += bool(citations)

                result.artifacts.setdefault("samples", []).append(
                    {
                        "id": case["id"],
                        "question": case["query"],
                        "answer": body,
                        "contexts": [h.text for h in hits],
                        "reference": case["reference"],
                    }
                )
                faithful = M.faithfulness(body, [observed_text])
                question_vec, answer_vec, reference_vec = await env.embedder.embed(
                    [case["query"], body or " ", case["reference"]]
                )
                correctness = M.factual_correctness(body, case["facts"], case["reference"])
                add("faithfulness", faithful)
                # Echo guard: an answer that only repeats the question is not relevant.
                echo = not (M.content_words(body) - M.content_words(case["query"]))
                add("answer_relevance", 0.0 if echo else M.cosine(question_vec, answer_vec))
                add("semantic_similarity", M.cosine(answer_vec, reference_vec))
                add("factual_correctness_f1", correctness["f1"])

                details: dict[str, Any] = {
                    "missing_facts": [f for f in case["facts"] if f not in found],
                    "answer": answer[:300], "faithfulness": round(faithful, 2),
                    "factual_correctness": {n: round(v, 2) for n, v in correctness.items()},
                }  # fmt: skip
                if judge_model is not None:
                    from evaluation import judges

                    contexts = [h.text for h in hits]
                    add(
                        "judge_faithfulness", await judges.faithfulness(judge_model, body, contexts)
                    )
                    add("judge_answer_relevance", await judges.answer_relevance(
                        judge_model, env.embedder, case["query"], body))  # fmt: skip
                    add("judge_factual_correctness", await judges.factual_correctness(
                        judge_model, body, case["reference"]))  # fmt: skip
                result.cases.append(
                    CaseResult(
                        case["id"],
                        len(found) == len(case["facts"])
                        and is_valid
                        and faithful >= 0.99
                        and correctness["f1"] >= 0.5,
                        details,
                    )
                )

            # Restricted pages synced from Confluence must never reach a member.
            admin = env.principals[Role.ADMIN]
            sync = data["sync"]
            async with env.database.session() as session:
                await sync_source(session, env.settings, env.embedder, admin, "confluence",
                                  env.hub.connectors["confluence"])  # fmt: skip
            leaks = 0
            for role in (Role.MEMBER, Role.TEAM_LEAD):
                async with env.database.session() as session:
                    hits = await env.knowledge(session).search(
                        env.principals[role], sync["restricted_query"], top_k=k
                    )
                leaked = any(h.title == sync["restricted_title"] for h in hits)
                leaks += leaked
                result.cases.append(CaseResult(f"sync-leak-{role.value}", not leaked, {}))
            async with env.database.session() as session:
                admin_hits = await env.knowledge(session).search(
                    admin, sync["restricted_query"], top_k=k
                )
            result.cases.append(CaseResult(
                "sync-admin-can-read", any(h.title == sync["restricted_title"] for h in admin_hits),
                {},
            ))  # fmt: skip

    def metric(name: str, description: str) -> Metric:
        values = scores.get(name, [])
        return Metric(name, M.mean(values), t.get(name), description=description,
                      ci=M.bootstrap_ci(values))  # fmt: skip

    result.metrics = [
        metric("context_recall", "Required facts present in the top-k chunks"),
        metric("context_precision_at_k", "Rank-aware share of chunks from relevant documents"),
        metric("faithfulness", "Answer claims supported by the context shown"),
        metric("answer_relevance", "Embedding similarity of question and answer"),
        metric("semantic_similarity", "Embedding similarity of answer and reference"),
        metric("factual_correctness_f1", "F1 of answer facts vs. reference facts"),
        Metric("citation_validity", ratio(valid, max(1, cited)), t["citation_validity"],
               description="Citations point at successful tool calls"),
        Metric("sync_leak_rate", ratio(leaks, 2), t["sync_leak_rate"], higher_is_better=False,
               description="Restricted synced documents reaching a member"),
    ]  # fmt: skip
    for name in ("judge_faithfulness", "judge_answer_relevance", "judge_factual_correctness"):
        if name in scores:
            result.metrics.append(metric(name, "LLM-as-judge (Ragas-style)"))
    result.seconds = timer.seconds
    return result
