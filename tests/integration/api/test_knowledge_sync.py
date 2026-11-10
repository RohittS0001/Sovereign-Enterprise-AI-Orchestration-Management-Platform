"""Syncing document sources into the knowledge base, with permission mirroring."""

from backend.app.core.security.permissions import Role
from backend.app.integrations.connectors.base import SourceDocument
from backend.app.main import create_app
from tests.conftest import serve


class Source:
    def __init__(self, documents: list[SourceDocument]) -> None:
        self.documents = documents

    async def list_documents(self) -> list[SourceDocument]:
        return list(self.documents)


def doc(source_id: str, text: str, restricted: bool = False) -> SourceDocument:
    return SourceDocument(source="confluence", id=source_id, title=f"Doc {source_id}",
                          text=text, restricted=restricted)  # fmt: skip


async def test_sync_adds_updates_removes_and_mirrors_restrictions(
    test_settings, database, make_user, auth_headers
) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    app = create_app(settings, database)
    source = Source([doc("1", "Freeze the refunds schema two days before go-live."),
                     doc("2", "Gateway volume discount tiers are confidential.", restricted=True)])  # fmt: skip
    app.state.integrations.connectors = {"confluence": source}
    pm = auth_headers(await make_user(role=Role.PROJECT_MANAGER))
    member = auth_headers(await make_user(role=Role.MEMBER))
    admin = auth_headers(await make_user(role=Role.ADMIN))

    async with serve(app) as client:
        first = (await client.post("/api/v1/knowledge/sync", json={}, headers=pm)).json()
        again = (await client.post("/api/v1/knowledge/sync", json={}, headers=pm)).json()
        source.documents = [doc("1", "Freeze the refunds schema three days before go-live.")]
        third = (await client.post("/api/v1/knowledge/sync", json={}, headers=pm)).json()
        denied = await client.post("/api/v1/knowledge/sync", json={}, headers=member)
        unknown = await client.post("/api/v1/knowledge/sync", json={"source": "nope"}, headers=pm)

        source.documents.append(
            doc("3", "Discount tiers: confidential pricing details.", restricted=True)
        )
        await client.post("/api/v1/knowledge/sync", json={}, headers=pm)
        query = {"query": "confidential discount tiers pricing"}
        member_hits = (
            await client.post("/api/v1/knowledge/search", json=query, headers=member)
        ).json()
        admin_hits = (
            await client.post("/api/v1/knowledge/search", json=query, headers=admin)
        ).json()

    assert (first[0]["added"], first[0]["removed"]) == (2, 0)
    assert (again[0]["unchanged"], again[0]["added"]) == (2, 0)
    assert (third[0]["updated"], third[0]["removed"]) == (1, 1)
    assert denied.status_code == 403 and unknown.status_code == 404
    assert all(r["title"] != "Doc 3" for r in member_hits["results"])  # restricted → admin-only
    assert any(r["title"] == "Doc 3" for r in admin_hits["results"])


async def test_demo_sources_sync(test_settings, database, make_user, auth_headers) -> None:
    settings = test_settings.model_copy(update={"demo_mode": True})
    pm = auth_headers(await make_user(role=Role.PROJECT_MANAGER))
    async with serve(create_app(settings, database)) as client:
        results = (await client.post("/api/v1/knowledge/sync", json={}, headers=pm)).json()

    by_source = {r["source"]: r for r in results}
    assert by_source["confluence"]["added"] == 2 and by_source["notion"]["added"] == 1
    assert not any(r["error"] for r in results)
