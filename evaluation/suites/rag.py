"""Retrieval-augmented answering: context quality, grounded citations, sync access control.

- **context_recall / context_precision** — do the top-k chunks contain the facts a correct
  answer needs, and how many come from the right documents?
- **citation_validity / faithfulness** — through the real agent pipeline (planner →
  ``knowledge.search`` via the gateway → responder) with a deterministic *extractive*
  responder that quotes the best sentence from what it was shown. This measures the RAG
  plumbing offline (redaction, untrusted wrapping, truncation and citation mapping must
  not lose or mis-attribute the facts); the ``agent`` suite grades a real model.
- **sync_leak_rate** — documents synced from a source as restricted must never reach a
  member through search.
"""

import re
from typing import Any

from backend.app.core.security.permissions import Role
from backend.app.domains.agent.service import AgentService
from backend.app.domains.knowledge.sync import sync_source
from backend.app.llm.base import AgentModels, ChatMessage
from backend.app.llm.fake import FakeLLM
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment

_CITE = re.compile(r"\[S(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def extractive_responder(query: str):
    """A responder that answers with the observation sentence overlapping the query most."""

    def reply(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
        prompt = messages[-1].content
        blocks = re.findall(r"\[S(\d+)\][^\n]*\n(.*?)(?=\n\[S\d+\]|\Z)", prompt, re.DOTALL)
        best, best_score, best_step = "", -1, "1"
        for step, block in blocks:
            for sentence in _SENTENCE.split(re.sub(r"<[^>]+>|[{}\[\]\"]", " ", block)):
                sentence = " ".join(sentence.split())
                score = len(_words(query) & _words(sentence))
                if 20 < len(sentence) < 400 and score > best_score:
                    best, best_score, best_step = sentence, score, step
        return f"{best} [S{best_step}]" if best else "I could not find this in the documents."

    return reply


async def run() -> SuiteResult:
    data = load_dataset("rag")
    result = SuiteResult("rag")
    k, t = data["k"], data["thresholds"]
    facts_found = facts_total = precise = shown = valid = cited = supported = answered = 0

    with Timer() as timer:
        async with environment() as env:
            for case in data["questions"]:
                principal = env.principals[Role(case["role"])]
                async with env.database.session() as session:
                    hits = await env.knowledge(session).search(principal, case["query"], top_k=k)
                context = " ".join(h.text for h in hits).lower()
                found = [f for f in case["facts"] if f.lower() in context]
                facts_found += len(found)
                facts_total += len(case["facts"])
                precise += sum(h.title in case["relevant_titles"] for h in hits)
                shown += len(hits)

                planner = FakeLLM(
                    [
                        {
                            "thought": "",
                            "done": False,
                            "steps": [
                                {
                                    "tool": "knowledge.search",
                                    "arguments": {"query": case["query"]},
                                    "purpose": "rag",
                                }
                            ],
                        },
                        {"thought": "enough", "done": True, "steps": []},
                    ]
                )
                reporter = FakeLLM(extractive_responder(case["query"]))
                async with env.database.session() as session:
                    run_ = await AgentService(
                        session, env.settings, AgentModels(planner, reporter), env.registry
                    ).run(principal, case["query"])
                answer = run_.answer or ""
                observed = reporter.calls[0]["messages"][-1].content if reporter.calls else ""
                citations = _CITE.findall(answer)
                ok_steps = {
                    s["step_id"].lstrip("S") for s in run_.steps if s["status"] == "success"
                }
                is_valid = bool(citations) and all(c in ok_steps for c in citations)
                body = _CITE.sub("", answer).strip()
                is_supported = bool(body) and " ".join(body.split()) in " ".join(
                    re.sub(r"<[^>]+>|[{}\[\]\"]", " ", observed).split()
                )
                valid += is_valid
                cited += bool(citations)
                supported += is_supported
                answered += 1
                result.cases.append(
                    CaseResult(
                        case["id"],
                        len(found) == len(case["facts"]) and is_valid and is_supported,
                        {
                            "missing_facts": [f for f in case["facts"] if f not in found],
                            "answer": answer[:300],
                        },
                    )
                )

            # Restricted pages synced from Confluence must never reach a member.
            admin = env.principals[Role.ADMIN]
            sync = data["sync"]
            async with env.database.session() as session:
                await sync_source(
                    session,
                    env.settings,
                    env.embedder,
                    admin,
                    "confluence",
                    env.hub.connectors["confluence"],
                )
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
            result.cases.append(
                CaseResult(
                    "sync-admin-can-read",
                    any(h.title == sync["restricted_title"] for h in admin_hits),
                    {},
                )
            )

    result.metrics = [
        Metric("context_recall", ratio(facts_found, facts_total), t["context_recall"]),
        Metric("context_precision", ratio(precise, shown), t["context_precision"]),
        Metric("citation_validity", ratio(valid, max(1, cited)), t["citation_validity"]),
        Metric("faithfulness", ratio(supported, answered), t["faithfulness"]),
        Metric("sync_leak_rate", ratio(leaks, 2), t["sync_leak_rate"], higher_is_better=False),
    ]
    result.seconds = timer.seconds
    return result
