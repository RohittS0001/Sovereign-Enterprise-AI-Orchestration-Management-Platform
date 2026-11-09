import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from backend.app.api.deps import PrincipalDep, SessionDep, SettingsDep, require_permissions
from backend.app.core.errors import AppException
from backend.app.core.security.permissions import Permission, Principal, Role
from backend.app.db.models import Visibility
from backend.app.domains.knowledge.parsers import extract_text
from backend.app.domains.knowledge.schemas import (
    DocumentPage,
    DocumentRead,
    DocumentTextCreate,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from backend.app.domains.knowledge.service import KnowledgeService

router = APIRouter(prefix="/api/v1/knowledge", tags=["Knowledge"])

Manager = Annotated[Principal, Depends(require_permissions(Permission.KNOWLEDGE_MANAGE))]
Reader = Annotated[Principal, Depends(require_permissions(Permission.KNOWLEDGE_READ))]


def _service(request: Request, session, settings) -> KnowledgeService:
    return KnowledgeService(
        session, settings, request.app.state.embedder, request.app.state.reranker
    )


def _read(document) -> DocumentRead:
    return DocumentRead.model_validate(document)


@router.post("/documents/text", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def add_text_document(
    body: DocumentTextCreate,
    request: Request,
    principal: Manager,
    session: SessionDep,
    settings: SettingsDep,
) -> DocumentRead:
    document = await _service(request, session, settings).ingest(
        principal,
        title=body.title,
        text=body.text,
        source_type="text",
        mime_type="text/plain",
        visibility=body.visibility,
        allowed_roles=[r.value for r in body.allowed_roles],
        tags=body.tags,
    )
    return _read(document)


@router.post("/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    principal: Manager,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description=".pdf, .docx, .md, .txt or .html")],
    title: Annotated[str | None, Form(max_length=300)] = None,
    visibility: Annotated[Visibility, Form()] = Visibility.ORGANIZATION,
    allowed_roles: Annotated[str, Form(description='JSON list, e.g. ["team_lead"]')] = "[]",
    tags: Annotated[str, Form(description="JSON list of strings")] = "[]",
) -> DocumentRead:
    max_bytes = settings.rag_max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppException(f"File exceeds {settings.rag_max_upload_mb} MB", 413, code="too_large")

    try:
        roles = [Role(r).value for r in json.loads(allowed_roles)]
        tag_list = [str(t)[:50] for t in json.loads(tags)][:20]
    except (ValueError, TypeError) as exc:
        raise AppException("allowed_roles and tags must be JSON lists of valid values", 422,
                           code="validation_error") from exc  # fmt: skip

    filename = file.filename or "upload.txt"
    text = extract_text(filename, data)
    document = await _service(request, session, settings).ingest(
        principal,
        title=title or filename.rsplit(".", 1)[0],
        text=text,
        source_type="upload",
        filename=filename,
        mime_type=file.content_type,
        visibility=visibility,
        allowed_roles=roles,
        tags=tag_list,
    )
    return _read(document)


@router.get("/documents", response_model=DocumentPage)
async def list_documents(
    request: Request,
    principal: Reader,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    documents, total = await _service(request, session, settings).list_documents(
        principal, limit=limit, offset=offset
    )
    return DocumentPage(
        items=[_read(d) for d in documents], total=total, limit=limit, offset=offset
    )


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    request: Request,
    principal: Reader,
    session: SessionDep,
    settings: SettingsDep,
) -> DocumentRead:
    return _read(await _service(request, session, settings).get(principal, document_id))


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    await _service(request, session, settings).delete(principal, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    request: Request,
    principal: Reader,
    session: SessionDep,
    settings: SettingsDep,
) -> SearchResponse:
    hits = await _service(request, session, settings).search(
        principal, body.query, top_k=body.top_k
    )
    return SearchResponse(query=body.query, results=[SearchResult(**hit.as_dict()) for hit in hits])
