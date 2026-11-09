"""Pure (no I/O) parts of the agent: observations, routing, verification, catalog."""

import asyncio

import pytest
from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.core.security.permissions import Permission
from backend.app.domains.agent.nodes.router import route_after_plan
from backend.app.domains.agent.nodes.verifier import verify_run
from backend.app.domains.agent.observations import (
    adf_to_text,
    compact,
    render_observations,
    to_text,
)
from backend.app.domains.agent.state import Observation, PlanStep
from backend.app.tools.base import ToolOperation, ToolResult, ToolStatus
from backend.app.tools.registry import ToolRegistry
from tests.support.agent import build_registry

# ------------------------------------------------------------------ observations


def test_adf_is_flattened_to_text() -> None:
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Line one"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Line two"}]},
        ],
    }

    assert adf_to_text(doc) == "Line one\nLine two"
    assert compact({"description": doc}) == {"description": "Line one\nLine two"}


def test_compact_drops_noise_and_empty_values() -> None:
    raw = {
        "self": "https://x",
        "key": "PHX-1",
        "fields": {"avatarUrls": {"48x48": "u"}, "summary": "S", "labels": [], "parent": None},
    }

    assert compact(raw) == {"key": "PHX-1", "fields": {"summary": "S"}}


def test_to_text_truncates() -> None:
    text = to_text({"body": "x" * 5000}, max_chars=600)

    assert len(text) < 700
    assert "[truncated" in text


def test_render_observations_wraps_untrusted_and_marks_status() -> None:
    rendered = render_observations(
        [
            Observation(
                step_id="S1", tool="jira.get_issue", purpose="p", status="success", content="{}"
            ),
            Observation(
                step_id="S2",
                tool="jira.create_task",
                purpose="p",
                status="pending_approval",
                approval_id="a-1",
            ),
            Observation(
                step_id="S3", tool="gmail.get_email", purpose="p", status="error", error="boom"
            ),
            Observation(
                step_id="S4",
                tool="gmail.get_email",
                purpose="p",
                status="success",
                content="x",
                suspicious=True,
            ),
        ]
    )

    assert '<untrusted_data source="jira.get_issue">' in rendered
    assert "Awaiting human approval (approval id a-1)" in rendered
    assert "Error: boom" in rendered
    assert "prompt-injection" in rendered


# ------------------------------------------------------------------ routing / verification


def step(i: int) -> PlanStep:
    return PlanStep(id=f"S{i}", tool="t", arguments={}, purpose="")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"pending": [step(1)]}, "execute"),
        ({"pending": [], "done_planning": True}, "verify"),
        ({"pending": [], "done_planning": False}, "plan"),
    ],
)
def test_route_after_plan(state, expected) -> None:
    assert route_after_plan(state) == expected


def obs(step_id: str, status: str, **kw) -> Observation:
    return Observation(step_id=step_id, tool="t", purpose="", status=status, **kw)


def test_verification_summarizes_outcomes() -> None:
    result = verify_run(
        {
            "observations": [
                obs("S1", "success"),
                obs("S2", "error", error="x"),
                obs("S3", "denied"),
                obs("S4", "pending_approval", approval_id="a1"),
            ]
        }
    )["verification"]

    assert result.ok is False
    assert result.failed_steps == ["S2"]
    assert result.denied_steps == ["S3"]
    assert result.pending_approvals == ["a1"]
    assert any("awaiting human approval" in n for n in result.notes)


def test_verification_ok_without_tools_or_with_successes() -> None:
    assert verify_run({"observations": []})["verification"].ok is True
    assert verify_run({"observations": [obs("S1", "success")]})["verification"].ok is True


# ------------------------------------------------------------------ catalog


def test_catalog_registers_tools_with_permissions(test_settings: Settings) -> None:
    registry = build_registry(test_settings)
    specs = {s.name: s for s in registry.specs()}

    assert specs["jira.search_issues"].operation == ToolOperation.READ
    assert specs["jira.search_issues"].permission == Permission.PROJECTS_READ
    assert specs["jira.create_task"].operation == ToolOperation.WRITE
    assert specs["jira.create_task"].permission == Permission.PROJECTS_WRITE
    assert specs["gmail.get_email"].permission == Permission.EMAIL_READ
    # 8 Jira writes + gmail.create_draft
    assert len([s for s in specs.values() if s.is_write]) == 9
    assert specs["gmail.create_draft"].permission == Permission.EMAIL_SEND
    assert all(s.description and s.input_model for s in specs.values())


def test_catalog_skips_unconfigured_integrations(test_settings: Settings) -> None:
    from backend.app.tools.catalog import build_tool_registry

    assert build_tool_registry(test_settings).list_tools() == []


# ------------------------------------------------------------------ registry safeguards


class Args(BaseModel):
    n: int


def flaky(fail_times: int, *, retryable: bool = True):
    calls = {"count": 0}

    async def handler(payload: dict) -> ToolResult:
        calls["count"] += 1
        if calls["count"] <= fail_times:
            return ToolResult(status=ToolStatus.ERROR, error="503", retryable=retryable)
        return ToolResult(status=ToolStatus.SUCCESS, data=payload)

    return handler, calls


async def test_registry_validates_and_normalizes_arguments() -> None:
    registry = ToolRegistry()
    handler, _ = flaky(0)
    registry.register("t", handler, input_model=Args)

    ok = await registry.invoke("t", {"n": "5"})
    bad = await registry.invoke("t", {"n": "five"})

    assert ok.data == {"n": 5}
    assert bad.status == ToolStatus.ERROR
    assert "Invalid arguments for t" in bad.error


async def test_reads_are_retried_on_transient_errors() -> None:
    registry = ToolRegistry(read_retries=2, retry_backoff=0)
    handler, calls = flaky(2)
    registry.register("read", handler)

    result = await registry.invoke("read", {})

    assert result.status == ToolStatus.SUCCESS
    assert calls["count"] == 3


async def test_non_retryable_errors_are_not_retried() -> None:
    registry = ToolRegistry(read_retries=2, retry_backoff=0)
    handler, calls = flaky(5, retryable=False)
    registry.register("read", handler)

    await registry.invoke("read", {})

    assert calls["count"] == 1


async def test_writes_are_never_retried() -> None:
    registry = ToolRegistry(read_retries=3, retry_backoff=0)
    handler, calls = flaky(1)
    registry.register("write", handler, operation=ToolOperation.WRITE)

    result = await registry.invoke("write", {})

    assert result.status == ToolStatus.ERROR
    assert calls["count"] == 1


async def test_slow_tools_time_out() -> None:
    async def slow(payload: dict) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult(status=ToolStatus.SUCCESS)

    registry = ToolRegistry()
    registry.register("slow", slow, timeout_seconds=0.05)

    result = await registry.invoke("slow", {})

    assert result.status == ToolStatus.ERROR
    assert "timed out" in result.error
    assert result.retryable is True


async def test_crashing_or_misbehaving_tools_are_contained() -> None:
    registry = ToolRegistry()
    registry.register("crash", lambda payload: 1 / 0)
    registry.register("weird", lambda payload: {"not": "a ToolResult"})

    crash = await registry.invoke("crash", {})
    weird = await registry.invoke("weird", {})

    assert crash.status == ToolStatus.ERROR
    assert "ZeroDivisionError" in crash.error
    assert "invalid result" in weird.error
