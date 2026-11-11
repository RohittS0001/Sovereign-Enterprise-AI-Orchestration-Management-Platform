"""Sync document sources (Confluence, Notion, SharePoint, Drive) into the knowledge base.

For each source the sync:

- adds new documents and re-ingests changed ones (matched by source + source id, compared
  by content checksum), so search always sees the current text;
- removes documents that disappeared from the source, but only after a *complete* listing
  (a capped or partial listing keeps the local copies);
- mirrors permissions: documents the source marks restricted become admin-only
  (``Visibility.ROLES`` with no roles), and so do documents whose ACL is unknown
  (``restricted is None``) unless the source is in ``DOCS_OPEN_SOURCES``; the rest get
  ``DOCS_DEFAULT_VISIBILITY`` / ``DOCS_DEFAULT_ROLES``. Access control stays in the SQL
  filter applied before ranking.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.errors import ConflictError
from backend.app.core.logging import get_logger
from backend.app.core.security.permissions import Principal
from backend.app.db.models import Document, Visibility
from backend.app.domains.governance.audit import actions
from backend.app.domains.governance.audit.service import AuditService
from backend.app.domains.knowledge.embeddings import Embedder
from backend.app.domains.knowledge.parsers import extract_text
from backend.app.domains.knowledge.service import KnowledgeService

logger = get_logger(__name__)


@dataclass
class SyncResult:
    source: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


def _visibility(
    settings: Settings, restricted: bool | None, source: str
) -> tuple[Visibility, list[str]]:
    if restricted is None:  # the ACL could not be read: fail closed unless the admin opted in
        restricted = source not in settings.docs_open_sources
    if restricted:
        return Visibility.ROLES, []  # admins only
    if settings.docs_default_visibility == "roles":
        return Visibility.ROLES, list(settings.docs_default_roles)
    return Visibility.ORGANIZATION, []


async def sync_source(
    session: AsyncSession,
    settings: Settings,
    embedder: Embedder | None,
    principal: Principal,
    name: str,
    service: Any,
) -> SyncResult:
    result = SyncResult(name)
    knowledge = KnowledgeService(session, settings, embedder)
    try:
        incoming = await service.list_documents()
    except Exception as exc:  # report per source, keep syncing the others
        result.error = getattr(exc, "message", None) or str(exc) or type(exc).__name__
        return result

    existing = {
        (doc.extra or {}).get("source_id"): doc
        for doc in await session.scalars(select(Document).where(Document.source_type == name))
    }
    seen: set[str] = set()
    for item in incoming:
        seen.add(item.id)
        text = item.text
        if not text.strip() and item.content is not None and item.filename:
            try:
                text = extract_text(item.filename, item.content)
            except Exception as exc:
                result.skipped.append(f"{item.title}: {getattr(exc, 'message', exc)}")
                continue
        if not text.strip():
            result.skipped.append(f"{item.title}: no text")
            continue
        current = existing.get(item.id)
        if current is not None:
            if current.checksum == hashlib.sha256(text.encode("utf-8")).hexdigest():
                result.unchanged += 1
                continue
            await session.delete(current)
            await session.flush()
        visibility, roles = _visibility(settings, item.restricted, name)
        try:
            document = await knowledge.ingest(
                principal, title=item.title[:300], text=text, source_type=name,
                filename=item.filename, visibility=visibility, allowed_roles=roles, tags=[name],
            )  # fmt: skip
        except ConflictError:
            result.skipped.append(f"{item.title}: same text already in the knowledge base")
            continue
        document.extra = {
            "source_id": item.id,
            "url": item.url,
            "restricted": item.restricted is not False,
            "updated": item.updated.isoformat() if item.updated else None,
        }
        result.updated += current is not None
        result.added += current is None

    complete = getattr(incoming, "complete", True)
    if not complete:
        result.skipped.append("listing incomplete (page cap or partial result): nothing removed")
    for source_id, document in existing.items():
        if complete and source_id not in seen:
            await session.delete(document)
            result.removed += 1

    await AuditService(session).record(
        actions.KNOWLEDGE_SYNC, status=actions.SUCCESS if not result.error else actions.FAILURE,
        principal=principal, resource_type="knowledge_source", resource_id=name,
        details={k: v for k, v in vars(result).items() if k != "source"},
    )  # fmt: skip
    await session.commit()
    logger.info(
        "Knowledge source synced",
        extra={
            "source": name,
            "added": result.added,
            "updated": result.updated,
            "removed": result.removed,
        },
    )
    return result
