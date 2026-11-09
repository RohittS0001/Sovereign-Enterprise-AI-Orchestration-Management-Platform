from fastapi import APIRouter, Request

from backend.app.core.config.settings import Settings
from backend.app.schemas.health import HealthResponse, ReadinessResponse
from backend.app.services.health_service import get_health_status, get_readiness_status

router = APIRouter(
    prefix="/api/v1",
    tags=["Health"],
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    return get_health_status(_settings(request))


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check(request: Request) -> ReadinessResponse:
    return get_readiness_status(_settings(request))
