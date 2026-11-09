from fastapi import APIRouter, Request

from backend.app.api.deps import SettingsDep
from backend.app.schemas.health import HealthResponse, ReadinessResponse
from backend.app.services.health_service import get_health_status, get_readiness_status

router = APIRouter(
    prefix="/api/v1",
    tags=["Health"],
)


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: SettingsDep) -> HealthResponse:
    return get_health_status(settings)


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check(request: Request, settings: SettingsDep) -> ReadinessResponse:
    return await get_readiness_status(settings, request.app.state.database)
