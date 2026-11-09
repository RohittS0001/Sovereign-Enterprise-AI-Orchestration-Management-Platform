"""Knowledge base API, permission-aware retrieval and the agent's knowledge.search tool."""

import io

import pytest
from sqlalchemy import select

from backend.app.core.security.permissions import Principal, Role
from backend.app.db.models import AuditEvent
from backend.app.main import create_app
from backend.app.tools.base import ToolStatus
from backend.app.tools.gateway import ToolGateway
from tests.conftest import serve
from tests.support.agent import DONE, models, plan

ESCALATION = """# Incident Escalation Policy
## Vendor incidents
When a vendor causes a SEV1 incident, escalate to the vendor account manager on day 1.
If no fix date arrives within 3 business days, switch to the fallback route.
## Communication
Customer updates are sent by the delivery lead."""

CONTRACT = """# Vendor Contract
## Penalties
Each SLA breach earns a service credit of 5% of the monthly fee."""

LUNCH = "# Cafeteria\nThe lunch menu changes every Monday."


@pytest.fixture
async def people(make_user, auth_headers):
    users = {
        role: await make_user(role=role)
        for role in (Role.ADMIN, Role.PROJECT_MANAGER, Role.TEAM_LEAD, Role.MEMBER, Role.AUDITOR)
    }
    return users, {role: auth_headers(user) for role, user in users.items()}


async def add(client, headers, title, text, **extra):
    return await client.post(
        "/api/v1/knowledge/documents/text",
        json={"title": title, "text": text, **extra},
        headers=headers,
    )


async def search(client, headers, query, top_k=5):
    response = await client.post(
        "/api/v1/knowledge/search", json={"query": query, "top_k": top_k}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def test_only_managers_can_add_documents(client, people) -> None:
    _, h = people

    assert (await add(client, h[Role.MEMBER], "t", "text")).status_code == 403
    assert (await add(client, h[Role.TEAM_LEAD], "t", "text")).status_code == 403
    created = await add(client, h[Role.PROJECT_MANAGER], "Escalation", ESCALATION)
    assert created.status_code == 201
    body = created.json()
    assert body["chunk_count"] >= 2
    assert body["embedding_model"] == "hashing-384"


async def test_duplicate_content_is_rejected(client, people) -> None:
    _, h = people
    await add(client, h[Role.ADMIN], "One", ESCALATION)

    duplicate = await add(client, h[Role.ADMIN], "Two", ESCALATION)

    assert duplicate.status_code == 409


async def test_search_returns_relevant_cited_passages(client, people) -> None:
    _, h = people
    for title, text in (("Escalation", ESCALATION), ("Cafeteria", LUNCH)):
        await add(client, h[Role.ADMIN], title, text)

    results = await search(client, h[Role.MEMBER], "what if the vendor gives no fix date")

    top = results[0]
    assert top["title"] == "Escalation"
    assert top["section"] == "Incident Escalation Policy > Vendor incidents"
    assert top["citation"] == "Escalation › Incident Escalation Policy > Vendor incidents"
    assert "fallback route" in top["text"]
    assert set(top["matched_by"]) == {"semantic", "keyword"}
    assert all(r["title"] != "Cafeteria" for r in results[:1])


async def test_role_restricted_documents_are_invisible_to_others(client, people) -> None:
    _, h = people
    await add(
        client,
        h[Role.ADMIN],
        "Contract",
        CONTRACT,
        visibility="roles",
        allowed_roles=["project_manager"],
    )

    member_hits = await search(client, h[Role.MEMBER], "SLA breach service credit")
    pm_hits = await search(client, h[Role.PROJECT_MANAGER], "SLA breach service credit")
    member_list = (await client.get("/api/v1/knowledge/documents", headers=h[Role.MEMBER])).json()

    assert member_hits == []
    assert pm_hits and pm_hits[0]["title"] == "Contract"
    assert member_list["total"] == 0


async def test_private_documents_belong_to_owner_and_admins(client, people) -> None:
    _, h = people
    doc = (
        await add(client, h[Role.PROJECT_MANAGER], "My notes", CONTRACT, visibility="private")
    ).json()

    other_pm_view = await client.get(
        f"/api/v1/knowledge/documents/{doc['id']}", headers=h[Role.TEAM_LEAD]
    )
    admin_view = await client.get(f"/api/v1/knowledge/documents/{doc['id']}", headers=h[Role.ADMIN])
    owner_hits = await search(client, h[Role.PROJECT_MANAGER], "service credit")
    lead_hits = await search(client, h[Role.TEAM_LEAD], "service credit")

    assert other_pm_view.status_code == 404
    assert admin_view.status_code == 200
    assert owner_hits and not lead_hits


async def test_delete_rules_and_audit(client, people, database) -> None:
    users, h = people
    doc = (await add(client, h[Role.PROJECT_MANAGER], "Escalation", ESCALATION)).json()

    by_member = await client.delete(
        f"/api/v1/knowledge/documents/{doc['id']}", headers=h[Role.MEMBER]
    )
    by_owner = await client.delete(
        f"/api/v1/knowledge/documents/{doc['id']}", headers=h[Role.PROJECT_MANAGER]
    )

    assert by_member.status_code == 403
    assert by_owner.status_code == 204
    assert await search(client, h[Role.ADMIN], "vendor fallback") == []
    async with database.session() as session:
        logged = [
            e.action for e in await session.scalars(select(AuditEvent).order_by(AuditEvent.seq))
        ]
    assert logged == ["knowledge.ingest", "knowledge.delete"]


async def test_upload_markdown_and_docx(client, people) -> None:
    from docx import Document

    _, h = people
    md = await client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "release.md",
                b"# Release\n## Freeze\nNo releases before sales.",
                "text/markdown",
            )
        },
        data={"visibility": "roles", "allowed_roles": '["team_lead"]', "tags": '["process"]'},
        headers=h[Role.ADMIN],
    )
    docx = Document()
    docx.add_heading("Runbook", level=1)
    docx.add_paragraph("Restart the payment worker when the queue backs up.")
    buffer = io.BytesIO()
    docx.save(buffer)
    word = await client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("runbook.docx", buffer.getvalue(), "application/octet-stream")},
        data={"title": "Worker runbook"},
        headers=h[Role.ADMIN],
    )

    assert md.status_code == 201
    assert md.json()["title"] == "release"
    assert md.json()["allowed_roles"] == ["team_lead"]
    assert md.json()["tags"] == ["process"]
    assert word.status_code == 201
    hits = await search(client, h[Role.MEMBER], "payment worker queue")
    assert hits[0]["title"] == "Worker runbook"


async def test_upload_rejections(test_settings, database, people) -> None:
    _, h = people
    settings = test_settings.model_copy(update={"rag_max_upload_mb": 1})

    async with serve(create_app(settings, database)) as client:
        exe = await client.post(
            "/api/v1/knowledge/documents", files={"file": ("x.exe", b"MZ")}, headers=h[Role.ADMIN]
        )
        big = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("big.txt", b"a" * (1024 * 1024 + 10))},
            headers=h[Role.ADMIN],
        )
        bad_roles = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("a.txt", b"hello world")},
            data={"allowed_roles": '["emperor"]'},
            headers=h[Role.ADMIN],
        )

    assert exe.status_code == 415
    assert big.status_code == 413
    assert bad_roles.status_code == 422


async def test_auditor_can_search_but_not_manage(client, people) -> None:
    _, h = people
    await add(client, h[Role.ADMIN], "Escalation", ESCALATION)

    assert await search(client, h[Role.AUDITOR], "vendor escalation")
    assert (await add(client, h[Role.AUDITOR], "x", "y")).status_code == 403


# ------------------------------------------------------------------ agent tool


async def test_knowledge_tool_applies_the_acting_users_permissions(
    app, client, people, database, test_settings
) -> None:
    users, h = people
    await add(
        client,
        h[Role.ADMIN],
        "Contract",
        CONTRACT,
        visibility="roles",
        allowed_roles=["project_manager"],
    )
    registry = app.state.tool_registry

    async with database.session() as session:
        gateway = ToolGateway(registry, session, test_settings)
        member = users[Role.MEMBER]
        pm = users[Role.PROJECT_MANAGER]
        as_member = await gateway.invoke(
            Principal.human(member.id, member.email, member.role).as_agent(),
            "knowledge.search", {"query": "service credit"},
        )  # fmt: skip
        as_pm = await gateway.invoke(
            Principal.human(pm.id, pm.email, pm.role).as_agent(),
            "knowledge.search", {"query": "service credit"},
        )  # fmt: skip

    assert as_member.status == ToolStatus.SUCCESS and as_member.data["results"] == []
    assert as_pm.data["results"][0]["title"] == "Contract"


async def test_knowledge_tool_refuses_without_acting_user(app) -> None:
    result = await app.state.tool_registry.invoke("knowledge.search", {"query": "anything"})

    assert result.status == ToolStatus.DENIED


async def test_agent_answers_from_documents(test_settings, database, people) -> None:
    _, h = people
    agent_models, _, reporting = models(
        [plan(("knowledge.search", {"query": "vendor no fix date"})), DONE],
        ["Switch to the fallback route after 3 business days [S1]."],
    )

    async with serve(create_app(test_settings, database, agent_models=agent_models)) as client:
        await add(client, h[Role.ADMIN], "Escalation", ESCALATION)
        run = (
            await client.post(
                "/api/v1/agent/query", json={"query": "What if the vendor has no fix date?"},
                headers=h[Role.MEMBER],
            )
        ).json()  # fmt: skip

    assert run["status"] == "completed"
    assert run["citations"] == ["S1"]
    evidence = reporting.calls[0]["messages"][1].content
    assert "Escalation › Incident Escalation Policy > Vendor incidents" in evidence
    assert "fallback route" in evidence


async def test_seed_script_loads_demo_documents(tmp_path, monkeypatch, make_user) -> None:
    from backend.app.core.config import Settings
    from backend.app.db.session import Database
    from scripts import seed_demo_knowledge as script

    url = f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}"
    db = Database(url)
    await db.create_all()
    from backend.app.core.security.passwords import hash_password
    from backend.app.db.models import User

    async with db.session() as session:
        session.add(
            User(
                email="root@example.com",
                full_name="Root",
                hashed_password=hash_password("x"),
                role=Role.ADMIN,
            )
        )
        await session.commit()
    await db.dispose()
    monkeypatch.setattr(script, "get_settings", lambda: Settings(_env_file=None, database_url=url))

    first = await script.seed("root@example.com")
    second = await script.seed("root@example.com")

    assert [o.split(":")[0] for o in first] == ["added"] * 3
    assert [o.split(":")[0] for o in second] == ["exists"] * 3
