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
        return {"thought": "", "done": False, "steps": [
            {"tool": t, "arguments": a, "purpose": "eval"} for t, a in steps]}  # fmt: skip

    def responder(messages, schema):
        if schema is not None:  # the judge
            return {"score": 5, "unsupported_claims": []}
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
