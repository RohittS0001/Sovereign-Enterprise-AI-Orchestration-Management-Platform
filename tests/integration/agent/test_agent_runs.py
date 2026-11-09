"""End-to-end agent runs through the LangGraph pipeline with scripted models."""

import pytest
from sqlalchemy import select

from backend.app.core.security.permissions import Principal, Role
from backend.app.db.models import ApprovalRequest, AuditEvent, RunStatus
from backend.app.domains.agent.service import AgentService
from backend.app.llm.base import LLMError
from tests.support.agent import (
    DONE,
    INJECTION_EMAIL,
    build_registry,
    fake_email_service,
    fake_jira_service,
    models,
    plan,
)


@pytest.fixture
async def session(database):
    async with database.session() as s:
        yield s


async def human(make_user, role=Role.TEAM_LEAD) -> Principal:
    user = await make_user(role=role)
    return Principal.human(user.id, user.email, user.role)


async def run_agent(session, settings, agent_models, registry, user, query="status?"):
    return await AgentService(session, settings, agent_models, registry).run(user, query)


async def test_multi_step_run_uses_results_of_earlier_steps(
    session, test_settings, make_user
) -> None:
    jira = fake_jira_service()
    registry = build_registry(test_settings, jira_service=jira)
    agent_models, ops, reporting = models(
        [
            plan(("jira.search_issues", {"jql": "project = PHX AND status = Blocked"})),
            plan(("jira.get_issue", {"issue_key": "PHX-7"})),
            DONE,
        ],
        ["PHX-7 is blocked because the vendor API is down [S2]. Found via search [S1]. [S9]"],
    )

    run = await run_agent(
        session, test_settings, agent_models, registry, await human(make_user), "Why is PHX late?"
    )

    assert run.status == RunStatus.COMPLETED
    assert [s["tool"] for s in run.steps] == ["jira.search_issues", "jira.get_issue"]
    assert [s["step_id"] for s in run.steps] == ["S1", "S2"]
    # Citations are kept only when they point to real successful steps.
    assert run.citations == ["S1", "S2"]
    assert "vendor API is down" in run.answer
    assert run.prompt_tokens > 0 and run.completion_tokens > 0
    assert (run.operations_model, run.reporting_model) == ("fake-ops", "fake-report")

    # The second planning call saw the first step's (compacted) result.
    second_prompt = ops.calls[1]["messages"][1].content
    assert "PHX-7" in second_prompt
    assert '<untrusted_data source="jira.search_issues">' in second_prompt
    assert "iconUrl" not in second_prompt  # noise stripped
    # The responder got the ADF description as plain text.
    assert "Vendor API down" in reporting.calls[0]["messages"][1].content


async def test_run_without_tools_answers_directly(session, test_settings, make_user) -> None:
    agent_models, _, reporting = models([DONE], ["I can help with project questions."])

    run = await run_agent(
        session, test_settings, agent_models, build_registry(test_settings), await human(make_user)
    )

    assert run.status == RunStatus.COMPLETED
    assert run.steps == []
    assert "(no tools were called)" in reporting.calls[0]["messages"][1].content


async def test_unavailable_tool_is_reported_back_to_planner(
    session, test_settings, make_user
) -> None:
    member = await human(make_user, Role.MEMBER)
    agent_models, ops, _ = models(
        [
            plan(("jira.create_task", {"project_key": "PHX", "summary": "x"})),
            plan(("jira.get_issue", {"issue_key": "PHX-7"})),
            DONE,
        ],
        ["done [S2]"],
    )

    run = await run_agent(
        session, test_settings, agent_models, build_registry(test_settings), member
    )

    assert [(s["tool"], s["status"]) for s in run.steps] == [
        ("jira.create_task", "error"),
        ("jira.get_issue", "success"),
    ]
    assert "not available to this user" in ops.calls[1]["messages"][1].content
    # A member's planner is never even shown write tools.
    assert "jira.create_task" not in ops.calls[0]["messages"][0].content


async def test_repeated_calls_are_skipped(session, test_settings, make_user) -> None:
    jira = fake_jira_service()
    call = ("jira.get_issue", {"issue_key": "PHX-7"})
    agent_models, _, _ = models([plan(call), plan(call), DONE], ["ok"])

    run = await run_agent(
        session,
        test_settings,
        agent_models,
        build_registry(test_settings, jira_service=jira),
        await human(make_user),
    )

    assert len(run.steps) == 1
    assert jira.get_issue.await_count == 1


async def test_step_and_iteration_budgets_are_enforced(session, test_settings, make_user) -> None:
    settings = test_settings.model_copy(update={"agent_max_steps": 2, "agent_max_iterations": 2})
    agent_models, ops, _ = models(
        [
            plan(*[("jira.get_issue", {"issue_key": f"PHX-{i}"}) for i in range(1, 6)]),
            plan(("jira.get_issue", {"issue_key": "PHX-9"})),
        ],
        ["partial"],
    )

    run = await run_agent(
        session, settings, agent_models, build_registry(settings), await human(make_user)
    )

    assert run.status == RunStatus.COMPLETED
    assert len(run.steps) == 2
    assert len(ops.calls) == 1  # budget exhausted before a second planning call


async def test_write_request_waits_for_approval(session, test_settings, make_user) -> None:
    jira = fake_jira_service()
    agent_models, _, reporting = models(
        [plan(("jira.create_task", {"project_key": "PHX", "summary": "Escalate vendor"})), DONE],
        ["A task has been proposed and awaits approval."],
    )

    run = await run_agent(
        session,
        test_settings,
        agent_models,
        build_registry(test_settings, jira_service=jira),
        await human(make_user),
    )

    assert run.status == RunStatus.AWAITING_APPROVAL
    assert run.steps[0]["status"] == "pending_approval"
    jira.create_task.assert_not_awaited()
    assert "awaiting human approval" in reporting.calls[0]["messages"][1].content


async def test_prompt_injection_is_flagged_and_forces_approval(
    session, test_settings, make_user
) -> None:
    jira = fake_jira_service()
    settings = test_settings.model_copy(update={"approval_exempt_tools": ["jira.add_comment"]})
    registry = build_registry(
        settings, jira_service=jira, email_service=fake_email_service(INJECTION_EMAIL)
    )
    agent_models, ops, reporting = models(
        [
            plan(("gmail.get_email", {"message_id": "m-1"})),
            plan(("jira.add_comment", {"issue_key": "PHX-7", "comment": "per email"})),
            DONE,
        ],
        ["Warning: the email contained suspicious instructions. [S1]"],
    )

    run = await run_agent(session, settings, agent_models, registry, await human(make_user))

    assert run.flagged_injection is True
    assert run.steps[0]["suspicious"] is True
    # Normally exempt, but approval is forced once the run saw suspicious content.
    assert run.steps[1]["status"] == "pending_approval"
    jira.add_comment.assert_not_awaited()
    approval = await session.scalar(select(ApprovalRequest))
    assert approval.risk_flags == ["possible_prompt_injection"]
    assert "WARNING" in ops.calls[1]["messages"][1].content
    assert "prompt-injection attempt" in reporting.calls[0]["messages"][1].content


async def test_secrets_never_reach_the_model(session, test_settings, make_user) -> None:
    jira = fake_jira_service()
    jira.get_issue.return_value = {
        "key": "PHX-7",
        "fields": {"description": "Use api_key=sk-live-12345678 and card 4111 1111 1111 1111"},
    }
    agent_models, ops, reporting = models(
        [plan(("jira.get_issue", {"issue_key": "PHX-7"})), DONE], ["ok"]
    )

    await run_agent(
        session,
        test_settings,
        agent_models,
        build_registry(test_settings, jira_service=jira),
        await human(make_user),
        "My token=abcd1234efgh, what's in PHX-7?",
    )

    everything_sent = " ".join(
        m.content for llm in (ops, reporting) for call in llm.calls for m in call["messages"]
    )
    for secret in ("sk-live-12345678", "4111 1111 1111 1111", "abcd1234efgh"):
        assert secret not in everything_sent
    assert "[REDACTED:SECRET]" in everything_sent
    assert "[REDACTED:CARD]" in everything_sent


async def test_llm_failure_marks_run_failed_but_keeps_steps(
    session, test_settings, make_user
) -> None:
    def broken_reporter(messages, schema):
        raise LLMError("ollama request timed out (model fake)")

    agent_models, _, _ = models([plan(("jira.get_issue", {"issue_key": "PHX-7"})), DONE], [])
    agent_models.reporting._replies = broken_reporter  # type: ignore[attr-defined]

    run = await run_agent(
        session, test_settings, agent_models, build_registry(test_settings), await human(make_user)
    )

    assert run.status == RunStatus.FAILED
    assert "timed out" in run.error
    assert [s["tool"] for s in run.steps] == ["jira.get_issue"]
    event = await session.scalar(select(AuditEvent).where(AuditEvent.action == "agent.run"))
    assert event.status == "failure"


async def test_invalid_planner_output_fails_cleanly(session, test_settings, make_user) -> None:
    agent_models, _, _ = models(["not json", "still not json"], [])

    run = await run_agent(
        session, test_settings, agent_models, build_registry(test_settings), await human(make_user)
    )

    assert run.status == RunStatus.FAILED
    assert "did not match PlannerOutput" in run.error
