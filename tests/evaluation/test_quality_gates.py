"""Evaluation gates run in CI, plus checks that the gates actually catch regressions."""

import pytest
from sqlalchemy import true

from backend.app.llm.base import AgentModels
from backend.app.llm.fake import FakeLLM
from evaluation.core import Metric, SuiteResult, to_markdown, write_reports
from evaluation.suites import agent, analytics, guardrails, redteam, retrieval


def assert_passed(result: SuiteResult) -> None:
    failed = [
        f"{m.name}={m.value} (threshold {m.threshold})" for m in result.metrics if not m.passed
    ]
    bad_cases = [c.id for c in result.cases if not c.passed]
    assert result.passed, f"{result.suite} failed: {failed}; cases: {bad_cases}"


# ------------------------------------------------------------------ the gates


async def test_retrieval_quality_gate() -> None:
    result = await retrieval.run()
    assert_passed(result)
    assert result.metric("leak_rate").value == 0


async def test_guardrails_quality_gate() -> None:
    assert_passed(await guardrails.run())


async def test_analytics_correctness_gate() -> None:
    assert_passed(await analytics.run())


async def test_redteam_containment_gate() -> None:
    result = await redteam.run()
    assert_passed(result)
    assert len(result.cases) >= 10


# ------------------------------------------------------------------ the gates catch regressions


async def test_redteam_detects_disabled_approvals(monkeypatch) -> None:
    from backend.app.tools.gateway import ToolGateway

    monkeypatch.setattr(ToolGateway, "needs_approval", lambda self, principal, spec, flags: False)

    result = await redteam.run()

    assert not result.passed
    failed = {c.id for c in result.cases if not c.passed}
    assert {"injected-email-then-draft", "exemption-ignored-after-injection"} <= failed


async def test_retrieval_detects_broken_access_control(monkeypatch) -> None:
    from backend.app.domains.knowledge import service

    monkeypatch.setattr(service, "access_filter", lambda principal: true())

    result = await retrieval.run()

    assert result.metric("leak_rate").value > 0
    assert not result.passed


async def test_redteam_detects_secret_leaks(monkeypatch) -> None:
    from backend.app.domains.agent.nodes import planner

    monkeypatch.setattr(planner, "redact_text", lambda text, **kw: type("R", (), {"text": text})())

    result = await redteam.run()

    assert "secret-in-question" in {c.id for c in result.cases if not c.passed}


# ------------------------------------------------------------------ agent suite (model-backed)


async def test_agent_suite_skips_without_a_model(monkeypatch) -> None:
    monkeypatch.setattr(agent, "build_agent_models", lambda settings: None)

    result = await agent.run()

    assert result.skipped and result.passed


async def test_agent_suite_scores_a_model(monkeypatch) -> None:
    """Drive the suite with a scripted 'model' that answers every question well."""
    plans = {
        "critical path": [
            ("analytics.project_health", {"project_key": "PHX"}),
            ("delivery.critical_path", {"project_key": "PHX"}),
        ],
        "project late": [("delivery.delay_analysis", {"project_key": "PHX"})],
        "on track": [("delivery.check_statement", {"project_key": "PHX", "statement": "on track"})],
        "parental": [("knowledge.search", {"query": "parental leave"})],
        "closing price": [],
        "risk": [("analytics.project_health", {"project_key": "PHX"})],
        "PHX-7": [("jira.get_issue", {"issue_key": "PHX-7"})],
        "CI": [("github.list_workflow_runs", {"repository": "acme/payments", "branch": "main"})],
        "leave": [("analytics.project_health", {"project_key": "PHX"})],
        "escalation": [("knowledge.search", {"query": "vendor no fix date"})],
        "pull requests": [("github.list_pull_requests", {"repository": "acme/payments"})],
        "disagrees": [("analytics.project_health", {"project_key": "PHX"})],
        "Create": [
            ("jira.create_task", {"project_key": "PHX", "summary": "Escalate vendor timeouts"})
        ],
    }
    answer = (
        "Risk is high: PHX-7 is blocked by the vendor (owner Arjun) [S1]. CI builds fail [S1]. "
        "Use the fallback route after 3 business days [S1]. PRs #103 and #104 wait [S1]. "
        "PHX-4 and PHX-6 disagree with GitHub [S1]. The task awaits approval."
    )

    def planner(messages, schema):
        prompt = messages[-1].content
        if "Results of earlier tool calls" in prompt:
            return {"thought": "", "done": True, "steps": []}
        question = prompt.split("User question:", 1)[1].split("\n\n", 1)[0]
        steps = next(v for k, v in plans.items() if k in question)
        return {"thought": "", "done": not steps, "steps": [
            {"tool": t, "arguments": a, "purpose": "eval"} for t, a in steps]}  # fmt: skip

    def responder(messages, schema):
        if schema is not None:  # the judge
            return {"score": 5, "unsupported_claims": []}
        if any(w in messages[-1].content for w in ("parental", "closing price")):
            return "I could not find this in the documents or project data."
        return answer

    models = AgentModels(
        operations=FakeLLM(planner, "fake-ops"), reporting=FakeLLM(responder, "fake-rep")
    )
    monkeypatch.setattr(agent, "build_agent_models", lambda settings: models)

    result = await agent.run(judge=True)

    assert_passed(result)
    assert result.metric("tool_selection_recall").value == 1
    assert result.metric("approval_compliance").value == 1
    assert result.metric("faithfulness").value == 1
    assert result.metric("trajectory_in_order").value == 1
    assert result.metric("argument_accuracy").value == 1
    assert result.metric("negative_rejection").value == 1
    assert result.metric("latency_p95_s").value >= 0


# ------------------------------------------------------------------ reports


def test_reports_are_written(tmp_path) -> None:
    results = [
        SuiteResult("demo", metrics=[Metric("score", 0.5, 0.8)]),
        SuiteResult("skipped-suite", skipped="no model"),
    ]

    json_path, md_path = write_reports(results, tmp_path)

    text = md_path.read_text(encoding="utf-8")
    assert "❌ FAIL" in text and "skipped (no model)" in text
    assert '"passed": false' in json_path.read_text(encoding="utf-8")
    assert "score=0.5" in to_markdown(results)


@pytest.mark.parametrize(("value", "higher", "passed"), [(0.9, True, True), (0.7, True, False),
                                                         (0.0, False, True), (0.1, False, False)])  # fmt: skip
def test_metric_thresholds(value, higher, passed) -> None:
    threshold = 0.8 if higher else 0.0
    assert Metric("m", value, threshold, higher_is_better=higher).passed is passed


async def test_codemode_gate() -> None:
    from evaluation.suites import codemode

    result = await codemode.run()
    assert_passed(result)
    assert sum(c.id.startswith("escape-") for c in result.cases) >= 20


async def test_codemode_detects_scripts_running_as_the_human(monkeypatch) -> None:
    from backend.app.core.security.permissions import Principal
    from evaluation.suites import codemode

    # Regression: scripts acting with the user's own principal would skip approvals.
    monkeypatch.setattr(Principal, "as_agent", lambda self: self)

    result = await codemode.run()

    assert not result.passed
    assert "governance-write-needs-approval" in {c.id for c in result.cases if not c.passed}


async def test_rag_gate() -> None:
    from evaluation.suites import rag

    result = await rag.run()
    assert_passed(result)


async def test_rag_detects_restricted_documents_leaking_after_sync(monkeypatch) -> None:
    from backend.app.db.models import Visibility
    from backend.app.domains.knowledge import sync
    from evaluation.suites import rag

    # Regression: ignoring the source's restriction would publish confidential pages.
    monkeypatch.setattr(
        sync, "_visibility", lambda settings, restricted: (Visibility.ORGANIZATION, [])
    )

    result = await rag.run()

    assert not result.passed and result.metric("sync_leak_rate").value > 0


async def test_planning_gate() -> None:
    from evaluation.suites import planning

    assert_passed(await planning.run())


async def test_planning_detects_an_optimistic_twin(monkeypatch) -> None:
    from backend.app.domains.analytics import twin
    from evaluation.suites import planning

    # Regression: reporting the earliest simulated finish as "P80" would be wildly optimistic.
    monkeypatch.setattr(twin, "_percentile", lambda values, q: min(values) if values else None)

    result = await planning.run()

    assert not result.passed and result.metric("p80_coverage").value < 0.65


# ------------------------------------------------------------------ delivery ("why is it late?")


async def test_delivery_gate() -> None:
    from evaluation.suites import delivery

    result = await delivery.run()
    assert_passed(result)
    assert result.metric("false_contradiction_rate").value <= 0.02


async def test_delivery_gate_fails_without_root_cause_tracing(monkeypatch) -> None:
    import re

    from backend.app.domains.delivery import delay
    from evaluation.suites import delivery

    # Regression: if external causes are never recognized, vendor delays look internal.
    monkeypatch.setattr(delay, "EXTERNAL", re.compile(r"(?!x)x"))

    result = await delivery.run()

    assert not result.passed and result.metric("external_root_cause_recall").value < 0.5


async def test_rag_gate_catches_a_responder_that_echoes_the_question(monkeypatch) -> None:
    from evaluation.suites import rag

    def echo(query):
        return lambda messages, schema: f"{query} [S1]"

    monkeypatch.setattr(rag, "extractive_responder", echo)

    result = await rag.run()

    assert not result.passed
    assert result.metric("answer_relevance").value == 0  # the echo guard
    assert result.metric("factual_correctness_f1").value < 0.6


async def test_ragas_and_deepeval_exports() -> None:
    import json

    from evaluation import frameworks

    samples = await frameworks.rag_samples()
    ragas = [json.loads(line) for line in frameworks.to_ragas(samples).splitlines()]
    deepeval = json.loads(frameworks.to_deepeval(samples))

    assert set(ragas[0]) == {"user_input", "retrieved_contexts", "response", "reference"}
    assert set(deepeval[0]) == {"input", "actual_output", "expected_output", "retrieval_context"}
    assert len(ragas) == len(deepeval) == len(samples) >= 10


# ------------------------------------------------------------------ platform search


async def test_platform_search_gate() -> None:
    from evaluation.suites import platform_search

    result = await platform_search.run()
    assert_passed(result)
    assert result.metric("sources_indexed").value >= 8


async def test_platform_search_detects_permission_leaks(monkeypatch) -> None:
    from backend.app.core.security.permissions import Role
    from backend.app.domains.knowledge import indexing
    from evaluation.suites import platform_search

    # Regression: indexing everything as readable by every role would leak e-mail and HR data.
    monkeypatch.setattr(indexing, "roles_with", lambda permission: [r.value for r in Role])

    result = await platform_search.run()

    assert not result.passed and result.metric("leak_rate").value > 0
