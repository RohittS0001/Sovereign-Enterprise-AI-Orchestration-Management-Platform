import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.app.core.security.permissions import Role
from backend.app.db.models import AgentRun, Report
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.reporting.scheduler import ReportScheduler
from backend.app.integrations.hub import build_integration_hub
from backend.app.llm.base import LLMError
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, models, plan
from tests.support.analytics import PHX_CONFIG


@pytest.fixture
def demo_settings(test_settings):
    return test_settings.model_copy(update={"demo_mode": True})


@pytest.fixture
async def staff(make_user, auth_headers):
    roles = (Role.ADMIN, Role.PROJECT_MANAGER, Role.TEAM_LEAD, Role.MEMBER, Role.AUDITOR)
    users = {role: await make_user(role=role) for role in roles}
    return users, {role: auth_headers(u) for role, u in users.items()}


async def configure_phx(client, headers):
    response = await client.put("/api/v1/projects/PHX", json=PHX_CONFIG, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ projects & health


async def test_project_config_permissions_and_validation(client, staff) -> None:
    _, h = staff

    assert (
        await client.put("/api/v1/projects/PHX", json=PHX_CONFIG, headers=h[Role.MEMBER])
    ).status_code == 403
    bad_repo = {**PHX_CONFIG, "github_repos": ["../secrets"]}
    assert (
        await client.put("/api/v1/projects/PHX", json=bad_repo, headers=h[Role.TEAM_LEAD])
    ).status_code == 422
    assert (
        await client.put("/api/v1/projects/bad key", json=PHX_CONFIG, headers=h[Role.TEAM_LEAD])
    ).status_code == 422

    created = await configure_phx(client, h[Role.TEAM_LEAD])
    updated = await client.put(
        "/api/v1/projects/phx", json={**PHX_CONFIG, "name": "Phoenix v2"}, headers=h[Role.TEAM_LEAD]
    )
    listing = (await client.get("/api/v1/projects", headers=h[Role.MEMBER])).json()

    assert created["key"] == "PHX"
    assert updated.json()["id"] == created["id"] and updated.json()["name"] == "Phoenix v2"
    assert [p["key"] for p in listing] == ["PHX"]


async def test_health_endpoint_on_demo_data(demo_settings, database, staff) -> None:
    _, h = staff

    async with serve(create_app(demo_settings, database)) as client:
        await configure_phx(client, h[Role.ADMIN])
        member = await client.get("/api/v1/projects/PHX/health", headers=h[Role.MEMBER])
        health = (
            await client.get("/api/v1/projects/PHX/health?fresh=true", headers=h[Role.TEAM_LEAD])
        ).json()
        missing = await client.get("/api/v1/projects/NOPE/health", headers=h[Role.TEAM_LEAD])

    assert member.status_code == 403  # analytics include people/leave data
    assert missing.status_code == 404
    assert health["risk"]["level"] in {"medium", "high"}
    assert {c["issue"] for c in health["contradictions"]} == {"PHX-4", "PHX-6"}
    assert health["forecast"]["committed_points"] == 31
    assert {m["name"] for m in health["workload"]["members"]} == {
        "Arjun M",
        "Meera S",
        "Sara K",
        "Dev R",
    }
    assert health["warnings"] == []


# ------------------------------------------------------------------ reports


async def test_generate_report_with_template_and_email_draft(
    demo_settings, database, staff
) -> None:
    _, h = staff

    async with serve(create_app(demo_settings, database)) as client:
        await configure_phx(client, h[Role.ADMIN])
        denied = await client.post(
            "/api/v1/reports/generate", json={"project_key": "PHX"}, headers=h[Role.MEMBER]
        )
        created = await client.post(
            "/api/v1/reports/generate", json={"project_key": "PHX"}, headers=h[Role.TEAM_LEAD]
        )
        report = created.json()
        listing = (
            await client.get("/api/v1/reports?project_key=PHX", headers=h[Role.MEMBER])
        ).json()
        fetched = await client.get(f"/api/v1/reports/{report['id']}", headers=h[Role.AUDITOR])
        draft = await client.post(
            f"/api/v1/reports/{report['id']}/email-draft", json={}, headers=h[Role.TEAM_LEAD]
        )
        member_draft = await client.post(
            f"/api/v1/reports/{report['id']}/email-draft", json={}, headers=h[Role.MEMBER]
        )

    assert denied.status_code == 403
    assert created.status_code == 201
    assert report["generated_by_model"] is None  # no LLM configured → deterministic template
    assert report["content_markdown"].startswith("# Weekly status — Phoenix Payments Revamp (PHX)")
    assert report["trigger"] == "manual"
    assert report["metrics"]["project"] == "PHX"
    assert listing["total"] == 1 and fetched.status_code == 200
    assert draft.json()["status"] == "success"
    assert draft.json()["data"]["to"] == ["leadership@acme.example"]
    assert member_draft.status_code == 403


async def test_report_narrative_uses_reporting_model(demo_settings, database, staff) -> None:
    _, h = staff
    agent_models, _, reporting = models([], ["## Summary\nPHX is at high risk (PHX-7 blocked)."])

    async with serve(create_app(demo_settings, database, agent_models=agent_models)) as client:
        await configure_phx(client, h[Role.ADMIN])
        report = (
            await client.post(
                "/api/v1/reports/generate", json={"project_key": "PHX"}, headers=h[Role.ADMIN]
            )
        ).json()

    assert report["generated_by_model"] == "fake-report"
    assert report["content_markdown"].startswith("## Summary")
    prompt = reporting.calls[0]["messages"][1].content
    assert '<untrusted_data source="analytics">' in prompt and "PHX-7" in prompt


async def test_report_falls_back_to_template_when_llm_fails(demo_settings, database, staff) -> None:
    _, h = staff

    def broken(messages, schema):
        raise LLMError("model offline")

    agent_models, _, _ = models([], [])
    agent_models.reporting._replies = broken  # type: ignore[attr-defined]

    async with serve(create_app(demo_settings, database, agent_models=agent_models)) as client:
        await configure_phx(client, h[Role.ADMIN])
        report = (
            await client.post(
                "/api/v1/reports/generate", json={"project_key": "PHX"}, headers=h[Role.ADMIN]
            )
        ).json()

    assert report["generated_by_model"] is None
    assert "Overall risk" in report["content_markdown"]


async def test_scheduler_generates_once_per_week(demo_settings, database, staff) -> None:
    _, h = staff
    async with serve(create_app(demo_settings, database)) as client:
        await configure_phx(client, h[Role.ADMIN])

    collector = SnapshotCollector(build_integration_hub(demo_settings), demo_settings)
    monday_9 = datetime(2026, 11, 9, 9, tzinfo=UTC)
    early = ReportScheduler(
        database, collector, demo_settings, None, clock=lambda: monday_9 - timedelta(hours=7)
    )
    due = ReportScheduler(database, collector, demo_settings, None, clock=lambda: monday_9)
    later = ReportScheduler(
        database, collector, demo_settings, None, clock=lambda: monday_9 + timedelta(days=2)
    )

    assert await early.run_due() == []  # Monday 02:00 UTC, before the 03:00 schedule
    assert await due.run_due() == ["PHX"]
    assert await later.run_due() == []  # already generated this week

    async with database.session() as session:
        reports = list(await session.scalars(select(Report)))
    assert [(r.trigger, str(r.period_start)) for r in reports] == [("scheduled", "2026-11-09")]


# ------------------------------------------------------------------ usage & quota


async def test_usage_summary_and_daily_quota(demo_settings, database, staff) -> None:
    users, h = staff
    settings = demo_settings.model_copy(update={"agent_daily_token_quota": 50})
    agent_models, _, _ = models(
        [plan(("analytics.list_projects", {})), DONE, DONE], ["a [S1]", "b"]
    )

    async with serve(create_app(settings, database, agent_models=agent_models)) as client:
        first = await client.post(
            "/api/v1/agent/query", json={"query": "what projects exist?"}, headers=h[Role.TEAM_LEAD]
        )
        async with database.session() as session:
            await session.execute(update(AgentRun).values(prompt_tokens=40, completion_tokens=20))
            await session.commit()
        blocked = await client.post(
            "/api/v1/agent/query", json={"query": "again"}, headers=h[Role.TEAM_LEAD]
        )
        other = await client.post(
            "/api/v1/agent/query", json={"query": "hi"}, headers=h[Role.MEMBER]
        )
        mine = (await client.get("/api/v1/usage/me/today", headers=h[Role.TEAM_LEAD])).json()
        admin_view = (await client.get("/api/v1/usage", headers=h[Role.ADMIN])).json()
        member_view = (await client.get("/api/v1/usage", headers=h[Role.MEMBER])).json()

    assert first.status_code == 200
    assert first.json()["steps"][0]["tool"] == "analytics.list_projects"
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "quota_exceeded"
    assert other.status_code == 200  # quotas are per user
    assert mine == {"used_tokens": 60, "quota": 50, "remaining": 0}
    assert admin_view["runs"] == 2 and len(admin_view["by_user"]) == 2
    assert [u["user_id"] for u in member_view["by_user"]] == [str(users[Role.MEMBER].id)]
    lead = next(u for u in admin_view["by_user"] if u["user_id"] == str(users[Role.TEAM_LEAD].id))
    assert (lead["tool_calls"], lead["prompt_tokens"] + lead["completion_tokens"]) == (1, 60)


async def test_agent_uses_project_health_tool(demo_settings, database, staff) -> None:
    _, h = staff
    agent_models, ops, reporting = models(
        [plan(("analytics.project_health", {"project_key": "PHX"})), DONE],
        ["PHX is high risk: PHX-7 is blocked and the sprint is off track [S1]."],
    )

    async with serve(create_app(demo_settings, database, agent_models=agent_models)) as client:
        await configure_phx(client, h[Role.ADMIN])
        run = (
            await client.post(
                "/api/v1/agent/query",
                json={"query": "Is Phoenix at risk?"},
                headers=h[Role.PROJECT_MANAGER],
            )
        ).json()

    assert run["status"] == "completed" and run["citations"] == ["S1"]
    evidence = reporting.calls[0]["messages"][1].content
    assert "off_track" in evidence and "merged_but_not_done" in evidence
    assert "analytics.project_health" in ops.calls[0]["messages"][0].content


async def test_scheduler_skips_a_failing_project_and_keeps_running(
    demo_settings, database, staff, monkeypatch
) -> None:
    _, h = staff
    async with serve(create_app(demo_settings, database)) as client:
        await configure_phx(client, h[Role.ADMIN])
    from backend.app.domains.reporting import scheduler as scheduler_module

    async def broken(*args, **kwargs):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(scheduler_module, "generate_scheduled", broken)
    collector = SnapshotCollector(build_integration_hub(demo_settings), demo_settings)
    due = ReportScheduler(
        database, collector, demo_settings, None,
        clock=lambda: datetime(2026, 11, 9, 9, tzinfo=UTC),
    )  # fmt: skip

    assert await due.run_due() == []  # logged and skipped, not raised


async def test_scheduler_task_starts_and_stops_cleanly(
    demo_settings, database, monkeypatch
) -> None:
    from backend.app.domains.reporting import scheduler as scheduler_module

    calls = []
    second_iteration = asyncio.Event()

    async def fake_run_due(self):
        calls.append(1)
        if len(calls) == 2:
            second_iteration.set()
        raise RuntimeError("one bad iteration must not kill the loop")

    monkeypatch.setattr(scheduler_module.ReportScheduler, "run_due", fake_run_due)
    monkeypatch.setattr(scheduler_module, "CHECK_INTERVAL_SECONDS", 0)
    collector = SnapshotCollector(build_integration_hub(demo_settings), demo_settings)
    scheduler = ReportScheduler(database, collector, demo_settings, None)

    scheduler.start()
    await asyncio.wait_for(second_iteration.wait(), timeout=5)
    await scheduler.stop()

    assert len(calls) >= 2


# ------------------------------------------------------------------ digital twin & negotiation


async def test_simulate_and_negotiate_endpoints(demo_settings, database, staff) -> None:
    _, h = staff
    async with serve(create_app(demo_settings, database)) as client:
        await configure_phx(client, h[Role.ADMIN])
        body = {"scenarios": [{"name": "add2", "add_people": 2}], "trials": 300}
        runs = await client.post(
            "/api/v1/projects/PHX/simulate", json=body, headers=h[Role.TEAM_LEAD]
        )
        denied = await client.post("/api/v1/projects/PHX/simulate", json={}, headers=h[Role.MEMBER])
        plan = await client.post(
            "/api/v1/projects/PHX/negotiate", json={}, headers=h[Role.TEAM_LEAD]
        )
        tool = await client.post(
            "/api/v1/projects/NOPE/simulate", json={}, headers=h[Role.TEAM_LEAD]
        )

    assert runs.status_code == 200, runs.text
    baseline, add2 = runs.json()
    assert baseline["scenario"] == "baseline" and add2["scenario"] == "add2"
    assert add2["p80"] <= baseline["p80"] and baseline["histogram"]
    assert denied.status_code == 403  # analytics:read
    assert plan.status_code == 200 and plan.json()["options"]
    assert "| Option |" in plan.json()["message_markdown"]
    assert tool.status_code == 404


async def test_project_links_are_validated_and_feed_production_health(
    demo_settings, database, staff
) -> None:
    _, h = staff
    async with serve(create_app(demo_settings, database)) as client:
        bad = await client.put("/api/v1/projects/PHX", json={**PHX_CONFIG, "links": {"nope": ["x"]}},
                               headers=h[Role.ADMIN])  # fmt: skip
        evil = await client.put("/api/v1/projects/PHX",
                                json={**PHX_CONFIG, "links": {"sentry_projects": ["../admin"]}},
                                headers=h[Role.ADMIN])  # fmt: skip
        saved = await configure_phx(client, h[Role.ADMIN])
        health = (await client.get("/api/v1/projects/PHX/health", headers=h[Role.TEAM_LEAD])).json()

    assert bad.status_code == 422 and evil.status_code == 422
    assert saved["links"]["sentry_projects"] == ["payments-api", "checkout-web"]
    production = health["production"]
    assert [i["id"] for i in production["incidents"]] == ["4211"]  # resolved one excluded
    assert {g["status"] for g in production["quality_gates"]} == {"ERROR"}
    factor = next(f for f in health["risk"]["factors"] if f["name"] == "Production health")
    assert factor["score"] > 0


# ------------------------------------------------------------------ chief of staff & voice briefing


async def test_portfolio_brief_and_drafted_escalations(demo_settings, database, staff) -> None:
    from backend.app.db.models import ApprovalRequest, ApprovalStatus

    _, h = staff
    async with serve(create_app(demo_settings, database)) as client:
        await configure_phx(client, h[Role.ADMIN])
        brief = (await client.get("/api/v1/briefing", headers=h[Role.TEAM_LEAD])).json()
        denied = await client.get("/api/v1/briefing", headers=h[Role.MEMBER])
        drafts = (
            await client.post("/api/v1/briefing/escalations", headers=h[Role.TEAM_LEAD])
        ).json()

    [phx] = brief["projects"]
    assert phx["key"] == "PHX" and phx["decision_needed"] and phx["top_risks"]
    assert brief["decisions"] == [phx["decision_needed"]]
    assert brief["spoken_text"].startswith("Good morning.") and "Phoenix" in brief["spoken_text"]
    assert "<" not in brief["spoken_text"] and "*" not in brief["spoken_text"]  # speakable
    assert denied.status_code == 403
    [draft] = drafts
    assert draft["status"] == "pending_approval" and draft["approval_id"]
    async with database.session() as session:
        approval = await session.scalar(select(ApprovalRequest))
    assert approval.tool_name == "gmail.create_draft" and approval.status == ApprovalStatus.PENDING
    assert approval.arguments["subject"].startswith("[Decision needed]")
