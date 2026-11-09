"""Prometheus scrape endpoint (outside /api/v1, as scrapers expect)."""

import hmac

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.app.api.deps import SettingsDep
from backend.app.core.errors import AuthenticationError, NotFoundError

router = APIRouter(tags=["Observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, settings: SettingsDep) -> Response:
    if not settings.metrics_enabled:
        raise NotFoundError("Not found")
    token = settings.metrics_token.get_secret_value()
    if token:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise AuthenticationError(
                "Metrics token required", headers={"WWW-Authenticate": "Bearer"}
            )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
