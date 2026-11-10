"""End-to-end agent quality with the real configured model (skipped when none is configured).

Per question the suite records which tools the planner called (with arguments), the answer,
latency and tokens. Metrics:

- **completion_rate** — runs that finished without error.
- **tool_selection_recall / precision / F1** — expected tools used vs. tools used.
- **trajectory_in_order** — expected tool sequence appears in order (agent trajectory eval).
- **argument_accuracy** — expected argument values passed to the expected tool.
- **fact_coverage** — expected facts (regexes) present in the answer.
- **citation_validity** — every ``[S#]`` points at a successful tool call.
- **approval_compliance** — writes stop at a pending approval instead of executing.
- **negative_rejection** — unanswerable questions get "I could not find…" instead of a guess.
- **latency p50/p95, tokens and steps per question** — cost and speed.
- **faithfulness** (``--judge``) — LLM 2 grades the answer against exactly the evidence the
  responder saw (a recording wrapper captures the prompt).
"""

import json
import re
import time

from pydantic import BaseModel, Field

from backend.app.core.security.permissions import Role
from backend.app.domains.agent.service import AgentService
from backend.app.llm.base import AgentModels, ChatMessage, LLMClient, LLMResponse
from backend.app.llm.factory import build_agent_models
from backend.app.llm.structured import generate_structured
from evaluation import metrics as M
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio
from evaluation.environment import environment

_CITATION = re.compile(r"\[(S\d+)\]")

JUDGE_SYSTEM = """You grade whether an answer is supported by the evidence.
Score 1-5: 5 = every factual claim is supported by the evidence, 1 = mostly unsupported.
List claims that the evidence does not support. Reply with JSON only."""


class RecordingModel:
    """Wraps a model to capture its prompts and replies (evidence for judges, planned calls)."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = inner.model
        self.last_prompt = ""
        self.replies: list[str] = []

    async def chat(self, messages: list[ChatMessage], **kwargs) -> LLMResponse:
        self.last_prompt = messages[-1].content
        response = await self.inner.chat(messages, **kwargs)
        self.replies.append(response.content)
        return response

    async def close(self) -> None:
        return None


def planned_calls(replies: list[str]) -> list[tuple[str, dict]]:
    """(tool, arguments) for every step the planner proposed, in order."""
    calls = []
    for reply in replies:
        try:
            plan = json.loads(reply)
        except (TypeError, ValueError):
            continue
        steps = plan.get("steps") if isinstance(plan, dict) else None
        for step in steps or []:
            if isinstance(step, dict):
                calls.append((step.get("tool", ""), step.get("arguments") or {}))
    return calls


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

        scores: dict[str, list[float]] = {}
        latencies: list[float] = []
        tokens: list[float] = []
        steps_used: list[float] = []

        def add(name: str, value: float) -> None:
            scores.setdefault(name, []).append(value)

        with Timer() as timer:
            for case in data["questions"]:
                planner = RecordingModel(models.operations)
                recorder = RecordingModel(models.reporting)
                principal = env.principals[Role(case["role"])]
                started = time.perf_counter()
                async with env.database.session() as session:
                    service = AgentService(
                        session, env.settings, AgentModels(planner, recorder), env.registry
                    )
                    run = await service.run(principal, case["question"])
                latencies.append(time.perf_counter() - started)
                tokens.append((run.prompt_tokens or 0) + (run.completion_tokens or 0))
                steps_used.append(len(run.steps))

                answer = run.answer or ""
                used = [
                    s["tool"] for s in run.steps if s["status"] in {"success", "pending_approval"}
                ]
                calls = planned_calls(planner.replies)
                ok_steps = {s["step_id"] for s in run.steps if s["status"] == "success"}
                cited = set(_CITATION.findall(answer))
                details: dict = {"status": run.status.value, "tools": used, "error": run.error}

                done = run.status.value != "failed"
                add("completion_rate", float(done))
                expected = case.get("expected_tools_any", [])
                tool_hit = not expected or any(t in used for t in expected)
                add("tool_selection_recall", float(tool_hit))
                if expected:
                    # "any of" expectations: the best-matching expected tool is the target.
                    target = next((t for t in expected if t in used), expected[0])
                    scored = M.tool_scores([target], used)
                    add("tool_selection_precision", scored["precision"])
                    add("tool_f1", scored["f1"])
                if case.get("expected_trajectory"):
                    match = M.trajectory_match(case["expected_trajectory"], used)
                    add("trajectory_in_order", float(match["in_order"]))
                    details["trajectory"] = match
                for tool, expected_args in (case.get("expected_args") or {}).items():
                    actual = next((a for t, a in calls if t == tool), {})
                    add("argument_accuracy", M.argument_accuracy(expected_args, actual))
                missing = [
                    f
                    for f in case.get("expected_facts", [])
                    if not re.search(f, answer, re.IGNORECASE)
                ]
                if case.get("expected_facts"):
                    add("fact_coverage", 1 - len(missing) / len(case["expected_facts"]))
                if ok_steps and not case.get("unanswerable"):
                    add("citation_validity", float(bool(cited) and cited <= ok_steps))
                    details["citations"] = sorted(cited)
                if case.get("expect_pending_approval"):
                    pending = any(s["status"] == "pending_approval" for s in run.steps)
                    add("approval_compliance", float(pending))
                    details["pending_approval"] = pending
                if case.get("unanswerable"):
                    add("negative_rejection", float(M.abstained(answer)))

                if judge and done and answer and not case.get("unanswerable"):
                    # Judge against exactly what the responder saw (tool results included).
                    verdict = await _judge(
                        models.reporting, case["question"], recorder.last_prompt, answer
                    )
                    add("faithfulness", (verdict.score - 1) / 4)
                    details["judge"] = verdict.model_dump()

                details["missing_facts"] = missing
                passed = done and tool_hit and not missing
                if case.get("unanswerable"):
                    passed = done and M.abstained(answer)
                result.cases.append(CaseResult(case["id"], passed, details))

    t = data["thresholds"]

    def metric(name: str) -> Metric:
        values = scores.get(name, [])
        return Metric(name, M.mean(values), t.get(name), ci=M.bootstrap_ci(values))

    names = [
        "completion_rate",
        "tool_selection_recall",
        "tool_selection_precision",
        "tool_f1",
        "trajectory_in_order",
        "argument_accuracy",
        "fact_coverage",
        "citation_validity",
        "approval_compliance",
        "negative_rejection",
        "faithfulness",
    ]
    result.metrics = [metric(n) for n in names if n in scores]
    result.metrics += [
        Metric(
            "latency_p50_s", round(M.percentile(latencies, 50), 2), None, higher_is_better=False
        ),
        Metric(
            "latency_p95_s",
            round(M.percentile(latencies, 95), 2),
            t.get("latency_p95_s"),
            higher_is_better=False,
        ),
        Metric(
            "tokens_per_question",
            round(M.mean(tokens), 1),
            t.get("tokens_per_question"),
            higher_is_better=False,
        ),
        Metric("steps_per_question", round(M.mean(steps_used), 2), None, higher_is_better=False),
        Metric("questions", ratio(len(latencies), 1), None),
    ]
    result.seconds = timer.seconds
    return result
