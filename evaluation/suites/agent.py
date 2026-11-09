"""End-to-end agent quality with the real configured model (skipped when none is configured).

Metrics: run completion, tool-selection recall, expected-fact coverage, citation validity,
approval compliance for writes, and (optionally) faithfulness graded by LLM 2 as a judge.
"""

import re

from pydantic import BaseModel, Field

from backend.app.core.security.permissions import Role
from backend.app.domains.agent.service import AgentService
from backend.app.llm.base import AgentModels, ChatMessage, LLMClient, LLMResponse
from backend.app.llm.factory import build_agent_models
from backend.app.llm.structured import generate_structured
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment

_CITATION = re.compile(r"\[(S\d+)\]")

JUDGE_SYSTEM = """You grade whether an answer is supported by the evidence.
Score 1-5: 5 = every factual claim is supported by the evidence, 1 = mostly unsupported.
List claims that the evidence does not support. Reply with JSON only."""


class RecordingModel:
    """Wraps the reporting model to capture the evidence the responder was shown."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = inner.model
        self.last_prompt = ""

    async def chat(self, messages: list[ChatMessage], **kwargs) -> LLMResponse:
        self.last_prompt = messages[-1].content
        return await self.inner.chat(messages, **kwargs)

    async def close(self) -> None:
        return None


class Verdict(BaseModel):
    score: int = Field(ge=1, le=5)
    unsupported_claims: list[str] = Field(default_factory=list)


async def _judge(model: LLMClient, question: str, evidence: str, answer: str) -> Verdict:
    return await generate_structured(
        model,
        [
            ChatMessage("system", JUDGE_SYSTEM),
            ChatMessage(
                "user", f"Question:\n{question}\n\nEvidence:\n{evidence}\n\nAnswer:\n{answer}"
            ),
        ],
        Verdict,
    )


async def run(*, judge: bool = False, real_embeddings: bool = False) -> SuiteResult:
    data = load_dataset("agent")
    result = SuiteResult("agent")

    async with environment(real_embeddings=real_embeddings) as env:
        models: AgentModels | None = build_agent_models(env.settings)
        if models is None:
            result.skipped = "no model configured (set LLM1_MODEL_NAME)"
            return result

        recorder = RecordingModel(models.reporting)
        run_models = AgentModels(operations=models.operations, reporting=recorder)
        completed = tools_ok = facts_hit = facts_total = 0
        citations_ok = citation_checks = writes_ok = write_checks = 0
        faithfulness: list[float] = []

        with Timer() as timer:
            for case in data["questions"]:
                principal = env.principals[Role(case["role"])]
                async with env.database.session() as session:
                    service = AgentService(session, env.settings, run_models, env.registry)
                    run = await service.run(principal, case["question"])

                answer = run.answer or ""
                used = [
                    s["tool"] for s in run.steps if s["status"] in {"success", "pending_approval"}
                ]
                ok_steps = {s["step_id"] for s in run.steps if s["status"] == "success"}
                cited = set(_CITATION.findall(answer))
                details: dict = {"status": run.status.value, "tools": used, "error": run.error}

                done = run.status.value != "failed"
                completed += done
                tool_hit = any(t in used for t in case["expected_tools_any"])
                tools_ok += tool_hit
                missing = [
                    f for f in case["expected_facts"] if not re.search(f, answer, re.IGNORECASE)
                ]
                facts_hit += len(case["expected_facts"]) - len(missing)
                facts_total += len(case["expected_facts"])
                if ok_steps:
                    citation_checks += 1
                    valid = bool(cited) and cited <= ok_steps
                    citations_ok += valid
                    details["citations"] = sorted(cited)
                if case.get("expect_pending_approval"):
                    write_checks += 1
                    pending = any(s["status"] == "pending_approval" for s in run.steps)
                    writes_ok += pending
                    details["pending_approval"] = pending

                if judge and done and answer:
                    # Judge against exactly what the responder saw (tool results included).
                    verdict = await _judge(
                        models.reporting, case["question"], recorder.last_prompt, answer
                    )
                    faithfulness.append((verdict.score - 1) / 4)
                    details["judge"] = verdict.model_dump()

                details["missing_facts"] = missing
                result.cases.append(
                    CaseResult(case["id"], done and tool_hit and not missing, details)
                )

    t = data["thresholds"]
    n = len(data["questions"])
    result.metrics = [
        Metric("completion_rate", ratio(completed, n), t["completion_rate"]),
        Metric("tool_selection_recall", ratio(tools_ok, n), t["tool_selection_recall"]),
        Metric("fact_coverage", ratio(facts_hit, facts_total), t["fact_coverage"]),
        Metric("citation_validity", ratio(citations_ok, citation_checks), t["citation_validity"]),
        Metric("approval_compliance", ratio(writes_ok, write_checks), t["approval_compliance"]),
    ]
    if faithfulness:
        result.metrics.append(
            Metric(
                "faithfulness", round(sum(faithfulness) / len(faithfulness), 4), t["faithfulness"]
            )
        )
    result.seconds = timer.seconds
    return result
