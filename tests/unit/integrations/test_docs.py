"""Document connectors against mocked APIs."""

import pytest

from backend.app.integrations.connectors.docs import (
    ConfluenceService,
    GoogleDriveService,
    NotionService,
    SharePointService,
    html_to_text,
)
from tests.support.connectors import MockApi


def test_confluence_storage_html_becomes_markdown_ish_text() -> None:
    text = html_to_text(
        "<h2>Rollback</h2><p>Roll back if latency &gt; 800&nbsp;ms.</p><ul><li>Page on-call</li></ul>"
    )
    assert text.splitlines()[0] == "## Rollback"
    assert "Roll back if latency > 800\xa0ms." in text and "Page on-call" in text


async def test_confluence_pages_follow_cursor_and_mirror_restrictions() -> None:
    page = lambda i: {"id": i, "title": f"Page {i}", "version": {"createdAt": "2026-11-01T00:00:00Z"},  # noqa: E731
                      "body": {"storage": {"value": f"<p>Body {i}</p>"}}, "_links": {"webui": f"/spaces/ENG/pages/{i}"}}  # fmt: skip
    api = MockApi({
        "GET /wiki/api/v2/spaces": {"results": [{"id": "77", "key": "ENG"}]},
        "GET /wiki/api/v2/spaces/77/pages": lambda r: (
            {"results": [page("1")], "_links": {"next": "/wiki/api/v2/spaces/77/pages?cursor=abc"}}
            if "cursor" not in str(r.url) else {"results": [page("2")], "_links": {}}),
        "GET /wiki/rest/api/content/1/restriction/byOperation/read":
            {"restrictions": {"user": {"results": []}, "group": {"results": []}}},
        "GET /wiki/rest/api/content/2/restriction/byOperation/read":
            {"restrictions": {"group": {"results": [{"name": "finance"}]}}},
    })  # fmt: skip
    svc = ConfluenceService(api.factory("Confluence", "https://acme.atlassian.net/wiki"),
                            "https://acme.atlassian.net/wiki", ["ENG"])  # fmt: skip

    docs = await svc.list_documents()

    assert [(d.id, d.restricted) for d in docs] == [("1", False), ("2", True)]
    assert (
        docs[0].url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/1"
        and docs[0].text == "Body 1"
    )
    with pytest.raises(ValueError):
        await ConfluenceService(
            api.factory("C", "https://x"), "https://x", ["ENG/../x"]
        ).list_documents()


async def test_notion_search_titles_and_blocks() -> None:
    api = MockApi({
        "POST /v1/search": {"results": [{"id": "pg1", "url": "https://notion.so/pg1",
                                         "last_edited_time": "2026-11-01T00:00:00Z",
                                         "properties": {"Name": {"type": "title", "title": [{"plain_text": "Agreements"}]}}}],
                            "has_more": False},
        "GET /v1/blocks/pg1/children": {"results": [
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Reviews"}]}},
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Within a day"}]}},
            {"type": "image", "image": {}}], "has_more": False},
    })  # fmt: skip
    svc = NotionService(api.factory("Notion", "https://api.notion.com"))

    [doc] = await svc.list_documents()

    assert doc.title == "Agreements" and doc.text == "## Reviews\n- Within a day"
    assert api.json_body(0)["filter"] == {"property": "object", "value": "page"}


async def test_sharepoint_downloads_supported_files_only() -> None:
    api = MockApi({
        "GET /v1.0/sites/site-1/drive/root/children": {"value": [
            {"id": "f1", "name": "Runbook.md", "file": {}, "webUrl": "https://sp/Runbook.md",
             "lastModifiedDateTime": "2026-11-02T00:00:00Z"},
            {"id": "f2", "name": "logo.png", "file": {}},
            {"id": "d1", "name": "Archive", "folder": {}}]},
        "GET /v1.0/sites/site-1/drive/items/f1/content": "# Runbook\nRestart the worker.",
    })  # fmt: skip
    svc = SharePointService(api.factory("SharePoint", "https://graph.microsoft.com/v1.0"), "site-1")

    [doc] = await svc.list_documents()

    assert (doc.title, doc.filename, doc.content) == (
        "Runbook",
        "Runbook.md",
        b"# Runbook\nRestart the worker.",
    )


async def test_google_drive_exports_docs_and_validates_the_folder() -> None:
    folder = "1AbCdEfGhIjKlMn"
    api = MockApi({
        "GET /drive/v3/files": {"files": [
            {"id": "g1", "name": "Design notes", "mimeType": "application/vnd.google-apps.document",
             "webViewLink": "https://docs.google.com/g1", "modifiedTime": "2026-11-03T00:00:00Z"}]},
        "GET /drive/v3/files/g1/export": "Plain text export",
    })  # fmt: skip
    svc = GoogleDriveService(api.factory("Google Drive", "https://www.googleapis.com"), folder)

    [doc] = await svc.list_documents()

    assert api.requests[0].url.params["q"] == f"'{folder}' in parents and trashed = false"
    assert (doc.text, doc.url) == ("Plain text export", "https://docs.google.com/g1")
    with pytest.raises(ValueError):
        GoogleDriveService(api.factory("G", "https://x"), "x' or '1'='1")
