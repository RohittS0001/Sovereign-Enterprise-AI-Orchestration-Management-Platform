"""Cloud monitoring endpoints on demo data: per project and the portfolio."""

from backend.app.core.security.permissions import Role
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.analytics import PHX_CONFIG


async def test_project_cloud_and_portfolio(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    admin = auth_headers(await make_user(role=Role.ADMIN))
    lead = auth_headers(await make_user(role=Role.TEAM_LEAD))
    member = auth_headers(await make_user(role=Role.MEMBER))
    async with serve(create_app(settings, database)) as client:
        await client.put("/api/v1/projects/PHX", json=PHX_CONFIG, headers=admin)
        as_admin = await client.get("/api/v1/projects/PHX/cloud", headers=admin)
        as_lead = await client.get("/api/v1/projects/PHX/cloud", headers=lead)
        denied = await client.get("/api/v1/projects/PHX/cloud", headers=member)
        missing = await client.get("/api/v1/projects/NOPE/cloud", headers=admin)
        overview = await client.get("/api/v1/portfolio", headers=admin)

    assert as_admin.status_code == 200, as_admin.text
    body = as_admin.json()
    assert len(body["resources"]) == 10 and body["cost"]["month_to_date"] > 0
    assert body["findings"][0]["severity"] == "critical"
    assert {r["purpose"] for r in body["resources"]} >= {"Payments API (capture + refunds)"}
    assert as_lead.json()["findings"] is None  # security findings: admins and auditors only
    assert denied.status_code == 403 and missing.status_code == 404
    row = overview.json()["projects"][0]
    assert row["project"] == "PHX" and row["resources"] == 10 and row["unhealthy"] == 2
    assert "Arjun M" in overview.json()["people"]
