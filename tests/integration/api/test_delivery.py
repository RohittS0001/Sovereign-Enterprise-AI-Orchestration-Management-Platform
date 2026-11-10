"""Delivery lifecycle endpoints on demo data: roadmap, DORA, release notes, retrospective,
and requirements → backlog that reaches Jira only through approvals."""

import pytest

from backend.app.core.security.permissions import Role
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import models
from tests.support.analytics import PHX_CONFIG

REQUIREMENTS = """# Refund disputes
- Merchants can open a dispute for a refund
- Support agents see a queue of open disputes
"""


@pytest.fixture
async def staff(make_user, auth_headers):
    roles = (Role.ADMIN, Role.TEAM_LEAD, Role.MEMBER)
    return {role: auth_headers(await make_user(role=role)) for role in roles}


@pytest.fixture
def demo_settings(test_settings):
    return test_settings.model_copy(update={"demo_mode": True})


async def test_read_endpoints_and_permissions(demo_settings, database, staff) -> None:
    async with serve(create_app(demo_settings, database)) as client:
        await client.put("/api/v1/projects/PHX", json=PHX_CONFIG, headers=staff[Role.ADMIN])
        lead = staff[Role.TEAM_LEAD]
        roadmap = await client.get("/api/v1/projects/PHX/roadmap", headers=lead)
        dora = await client.get("/api/v1/projects/PHX/dora?window_days=30", headers=lead)
        notes = await client.get("/api/v1/projects/PHX/release-notes", headers=lead)
        retro = await client.get("/api/v1/projects/PHX/retrospective", headers=lead)
        member = await client.get("/api/v1/projects/PHX/roadmap", headers=staff[Role.MEMBER])
        bad_window = await client.get("/api/v1/projects/PHX/dora?window_days=1", headers=lead)
        bad_range = await client.get(
            "/api/v1/projects/PHX/release-notes?since=2026-11-09&until=2026-11-01", headers=lead
        )
        missing = await client.get("/api/v1/projects/NOPE/roadmap", headers=lead)

    assert roadmap.status_code == 200, roadmap.text
    assert roadmap.json()["critical_path"][-1] == "PHX-5"
    assert dora.status_code == 200 and dora.json()["lead_time"]["value"] == 3.5
    assert "# Phoenix" in notes.json()["markdown"]
    body = retro.json()
    assert body["written_by"] == "template" and body["committed_points"] == 31
    assert any("Blocked work" in item for item in body["to_improve"]) and body["actions"]
    assert member.status_code == 403
    assert bad_window.status_code == 422 and bad_range.status_code == 422
    assert missing.status_code == 404


async def test_backlog_is_drafted_then_submitted_as_pending_approvals(
    demo_settings, database, staff
) -> None:
    async with serve(create_app(demo_settings, database)) as client:
        await client.put("/api/v1/projects/PHX", json=PHX_CONFIG, headers=staff[Role.ADMIN])
        lead = staff[Role.TEAM_LEAD]
        draft = await client.post(
            "/api/v1/projects/PHX/backlog/draft", json={"requirements": REQUIREMENTS}, headers=lead
        )
        proposal = draft.json()
        submitted = await client.post(
            "/api/v1/projects/PHX/backlog/submit", json=proposal, headers=lead
        )
        approvals = await client.get("/api/v1/approvals?status=pending", headers=lead)
        empty = await client.post(
            "/api/v1/projects/PHX/backlog/submit", json={"epics": []}, headers=lead
        )
        member = await client.post(
            "/api/v1/projects/PHX/backlog/draft",
            json={"requirements": REQUIREMENTS},
            headers=staff[Role.MEMBER],
        )

    assert draft.status_code == 200, draft.text
    assert proposal["drafted_by"] == "parser" and proposal["total_points"] > 0
    assert [s["title"] for s in proposal["epics"][0]["stories"]][0].startswith("Merchants")
    results = submitted.json()
    assert submitted.status_code == 200, submitted.text
    assert [r["tool"] for r in results] == [
        "jira.create_epic",
        "jira.create_story",
        "jira.create_story",
    ]
    assert all(r["status"] == "pending_approval" and r["approval_id"] for r in results)
    pending = {a["tool_name"] for a in approvals.json()["items"]}
    assert {"jira.create_epic", "jira.create_story"} <= pending
    assert empty.status_code == 422
    assert member.status_code == 403


async def test_retrospective_and_draft_use_the_reporting_model(
    demo_settings, database, staff
) -> None:
    epic = {"epics": [{"title": "Disputes", "stories": [{"title": "Open a dispute"}]}]}
    agent_models, _, reporting = models([], ["Blameless summary.", epic])
    app = create_app(demo_settings, database, agent_models=agent_models)
    async with serve(app) as client:
        await client.put("/api/v1/projects/PHX", json=PHX_CONFIG, headers=staff[Role.ADMIN])
        lead = staff[Role.TEAM_LEAD]
        retro = (await client.get("/api/v1/projects/PHX/retrospective", headers=lead)).json()
        draft = (
            await client.post(
                "/api/v1/projects/PHX/backlog/draft",
                json={"requirements": REQUIREMENTS},
                headers=lead,
            )
        ).json()

    assert retro["narrative"] == "Blameless summary." and retro["written_by"] == "fake-report"
    assert "<untrusted_data" in reporting.calls[0]["messages"][1].content
    assert draft["drafted_by"] == "fake-report" and draft["epics"][0]["title"] == "Disputes"
