"""Document sources for the knowledge base: Confluence, Notion, SharePoint/OneDrive, Drive.

Each service lists documents as ``SourceDocument`` (text, or raw bytes + filename for the
knowledge parsers) with a ``restricted`` flag when the source limits who may read it.
The knowledge sync (``domains/knowledge/sync.py``) maps that to platform visibility, so
search keeps applying access control before ranking.
"""

import html
import re
from typing import Any

from backend.app.core.config import Settings
from backend.app.integrations.connectors.auth import GoogleServiceAccountToken, microsoft_token
from backend.app.integrations.connectors.base import (
    ConnectorClient,
    ConnectorService,
    SourceDocument,
    safe_id,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec

SUPPORTED_FILES = (".md", ".markdown", ".txt", ".pdf", ".docx")
MAX_DOCUMENTS = 500

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|tr|h[1-6]|br|table|ul|ol)>|<br\s*/?>", re.IGNORECASE)
_HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)


def html_to_text(markup: str) -> str:
    """Confluence storage format (XHTML) → plain text with Markdown headings for chunking."""
    markup = _HEADING.sub(
        lambda m: "\n" + "#" * int(m.group(1)) + " " + _TAG.sub("", m.group(2)) + "\n", markup
    )
    markup = _BLOCK_END.sub("\n", markup)
    text = html.unescape(_TAG.sub("", markup))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


# ------------------------------------------------------------------ Confluence


class ConfluenceService(ConnectorService):
    source = "confluence"

    def __init__(self, client_factory: Any, base_url: str, spaces: list[str]) -> None:
        super().__init__(client_factory)
        self.base_url = base_url.rstrip("/")
        self.spaces = spaces

    async def _restricted(self, page_id: str) -> bool:
        body = await self.client.get(f"/rest/api/content/{page_id}/restriction/byOperation/read")
        restrictions = body.get("restrictions") or {}
        return any((restrictions.get(kind) or {}).get("results") for kind in ("user", "group"))

    async def list_documents(self, limit: int = MAX_DOCUMENTS) -> list[SourceDocument]:
        if not self.spaces:
            raise ValueError("Set CONFLUENCE_SPACES to the space keys to sync")
        keys = ",".join(safe_id(k, "Confluence space") for k in self.spaces)
        spaces = (await self.client.get("/api/v2/spaces", {"keys": keys})).get("results") or []
        documents: list[SourceDocument] = []
        for space in spaces:
            params: dict[str, Any] | None = {"body-format": "storage", "limit": 50}
            path = f"/api/v2/spaces/{space['id']}/pages"
            while path and len(documents) < limit:
                body = await self.client.get(path, params)
                for page in body.get("results") or []:
                    page_id = str(page.get("id"))
                    documents.append(SourceDocument(
                        source=self.source, id=page_id, title=page.get("title", "Untitled"),
                        text=html_to_text(((page.get("body") or {}).get("storage") or {}).get("value", "")),
                        url=self.base_url + ((page.get("_links") or {}).get("webui") or ""),
                        updated=to_date((page.get("version") or {}).get("createdAt")),
                        restricted=await self._restricted(page_id),
                    ))  # fmt: skip
                next_link = (body.get("_links") or {}).get("next")
                path = next_link.removeprefix("/wiki") if next_link else ""
                params = None  # the next link carries its own cursor
        return documents[:limit]

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/api/v2/spaces", {"limit": 1})
        return {"spaces_visible": len(body.get("results") or [])}


# ------------------------------------------------------------------ Notion

_NOTION_TEXT = {"paragraph": "", "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
                "bulleted_list_item": "- ", "numbered_list_item": "1. ", "to_do": "- [ ] ",
                "quote": "> ", "callout": "", "code": ""}  # fmt: skip


def notion_title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title") or []) or "Untitled"
    return "Untitled"


class NotionService(ConnectorService):
    source = "notion"

    async def _text(self, page_id: str) -> str:
        lines: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            body = await self.client.get(
                f"/v1/blocks/{safe_id(page_id, 'Notion page')}/children", params
            )
            for block in body.get("results") or []:
                kind = block.get("type")
                if kind in _NOTION_TEXT:
                    text = "".join(
                        t.get("plain_text", "")
                        for t in (block.get(kind) or {}).get("rich_text") or []
                    )
                    if text:
                        lines.append(_NOTION_TEXT[kind] + text)
            if not body.get("has_more") or len(lines) > 5000:
                return "\n".join(lines)
            cursor = body.get("next_cursor")

    async def list_documents(self, limit: int = MAX_DOCUMENTS) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        cursor: str | None = None
        while len(documents) < limit:
            payload: dict[str, Any] = {
                "filter": {"property": "object", "value": "page"},
                "page_size": 50,
            }
            if cursor:
                payload["start_cursor"] = cursor
            body = await self.client.post("/v1/search", payload)
            for page in body.get("results") or []:
                documents.append(SourceDocument(
                    source=self.source, id=page.get("id", ""), title=notion_title(page),
                    text=await self._text(page.get("id", "")), url=page.get("url"),
                    updated=to_date(page.get("last_edited_time")),
                ))  # fmt: skip
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
        return documents[:limit]

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/v1/users/me")
        return {"bot": body.get("name")}


# ------------------------------------------------------------------ SharePoint / OneDrive


class SharePointService(ConnectorService):
    source = "sharepoint"

    def __init__(self, client_factory: Any, site_id: str, drive_id: str = "") -> None:
        super().__init__(client_factory)
        self.drive = (f"/drives/{safe_id(drive_id, 'drive id')}" if drive_id
                      else f"/sites/{safe_id(site_id, 'site id')}/drive")  # fmt: skip

    async def list_documents(self, limit: int = MAX_DOCUMENTS) -> list[SourceDocument]:
        body = await self.client.get(
            f"{self.drive}/root/children",
            {"$select": "id,name,file,lastModifiedDateTime,webUrl", "$top": min(limit, 200)},
        )
        documents = []
        for item in body.get("value") or []:
            name = item.get("name", "")
            if "file" not in item or not name.lower().endswith(SUPPORTED_FILES):
                continue
            content = await self.client.get_bytes(f"{self.drive}/items/{item['id']}/content")
            documents.append(SourceDocument(
                source=self.source, id=item["id"], title=name.rsplit(".", 1)[0], text="",
                content=content, filename=name, url=item.get("webUrl"),
                updated=to_date(item.get("lastModifiedDateTime")),
            ))  # fmt: skip
        return documents

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(self.drive, {"$select": "id,name"})
        return {"drive": body.get("name")}


# ------------------------------------------------------------------ Google Drive

_GOOGLE_DOC = "application/vnd.google-apps.document"
_FOLDER = re.compile(r"^[A-Za-z0-9_-]{10,100}$")


class GoogleDriveService(ConnectorService):
    source = "google_drive"

    def __init__(self, client_factory: Any, folder_id: str) -> None:
        super().__init__(client_factory)
        if not _FOLDER.fullmatch(folder_id):
            raise ValueError("GOOGLE_DRIVE_FOLDER_ID looks invalid")
        self.folder_id = folder_id

    async def list_documents(self, limit: int = MAX_DOCUMENTS) -> list[SourceDocument]:
        body = await self.client.get("/drive/v3/files", {
            "q": f"'{self.folder_id}' in parents and trashed = false",
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            "pageSize": min(limit, 100), "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        })  # fmt: skip
        documents = []
        for f in body.get("files") or []:
            name, mime = f.get("name", ""), f.get("mimeType", "")
            if mime == _GOOGLE_DOC:
                text = await self.client.get_text(
                    f"/drive/v3/files/{f['id']}/export?mimeType=text/plain"
                )
                doc = SourceDocument(source=self.source, id=f["id"], title=name, text=text)
            elif name.lower().endswith(SUPPORTED_FILES):
                content = await self.client.get_bytes(f"/drive/v3/files/{f['id']}?alt=media")
                doc = SourceDocument(source=self.source, id=f["id"], title=name.rsplit(".", 1)[0],
                                     text="", content=content, filename=name)  # fmt: skip
            else:
                continue
            doc.url, doc.updated = f.get("webViewLink"), to_date(f.get("modifiedTime"))
            documents.append(doc)
        return documents

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(f"/drive/v3/files/{self.folder_id}", {"fields": "id,name",
                                                                          "supportsAllDrives": "true"})  # fmt: skip
        return {"folder": body.get("name")}


# ------------------------------------------------------------------ demo twin


class DemoDocs:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.documents = [SourceDocument(source=source, **d) for d in data.get("documents", [])]

    async def list_documents(self, limit: int = MAX_DOCUMENTS) -> list[SourceDocument]:
        return self.documents[:limit]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _demo(source: str) -> Any:
    return lambda data, today: DemoDocs(source, data.get("docs", {}).get(source, {}))


def _client(s: Settings, name: str, base_url: str, **kwargs: Any) -> Any:
    return lambda: ConnectorClient(
        name, base_url=base_url, timeout=s.connector_timeout_seconds, **kwargs
    )


def _google_drive(s: Settings) -> GoogleDriveService:
    token = GoogleServiceAccountToken(s.google_service_account_file,  # type: ignore[arg-type]
                                      ["https://www.googleapis.com/auth/drive.readonly"],
                                      s.google_delegated_user)  # fmt: skip
    return GoogleDriveService(_client(s, "Google Drive", "https://www.googleapis.com", token_provider=token),
                              s.google_drive_folder_id)  # fmt: skip


DOCS_SPECS = (
    ConnectorSpec(
        "confluence", "Confluence Cloud", "docs",
        required=("confluence_url", "confluence_email", "confluence_api_token", "confluence_spaces"),
        setup="Atlassian API token (can reuse the Jira one); CONFLUENCE_URL=https://<site>.atlassian.net/wiki; "
              "page read-restrictions are mirrored (restricted pages become admin-only)",
        docs_url="https://developer.atlassian.com/cloud/confluence/rest/v2/",
        build=lambda s: ConfluenceService(_client(s, "Confluence", s.confluence_url,
                                                  auth=(s.confluence_email, s.confluence_api_token.get_secret_value())),
                                          s.confluence_url, s.confluence_spaces),
        demo=_demo("confluence"),
    ),
    ConnectorSpec(
        "notion", "Notion", "docs", required=("notion_token",), optional=("notion_version",),
        setup="notion.so/my-integrations → internal integration; share the pages to sync with it",
        docs_url="https://developers.notion.com/reference/intro",
        build=lambda s: NotionService(_client(s, "Notion", "https://api.notion.com", headers={
            "Authorization": f"Bearer {s.notion_token.get_secret_value()}", "Notion-Version": s.notion_version})),
        demo=_demo("notion"),
    ),
    ConnectorSpec(
        "sharepoint", "SharePoint / OneDrive", "docs",
        required=("ms_tenant_id", "ms_client_id", "ms_client_secret", "sharepoint_site_id"),
        optional=("sharepoint_drive_id",),
        setup="Entra ID app registration with application permission Sites.Selected (granted to the "
              "site) or Sites.Read.All; reads .md/.txt/.pdf/.docx in the library root",
        docs_url="https://learn.microsoft.com/graph/api/resources/sharepoint",
        build=lambda s: SharePointService(_client(s, "SharePoint", "https://graph.microsoft.com/v1.0",
                                                  token_provider=microsoft_token(s)),
                                          s.sharepoint_site_id, s.sharepoint_drive_id),
        demo=_demo("sharepoint"),
    ),
    ConnectorSpec(
        "google_drive", "Google Drive", "docs",
        required=("google_service_account_file", "google_drive_folder_id"), optional=("google_delegated_user",),
        setup="Service account JSON key; share the folder with the service account (or use "
              "domain-wide delegation with GOOGLE_DELEGATED_USER)",
        docs_url="https://developers.google.com/drive/api/reference/rest/v3",
        build=_google_drive,
        demo=_demo("google_drive"),
    ),
)  # fmt: skip
