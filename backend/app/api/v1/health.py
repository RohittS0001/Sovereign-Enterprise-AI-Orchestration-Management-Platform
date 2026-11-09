from fastapi import APIRouter, Request

from backend.app.api.deps import SettingsDep
from backend.app.domains.system.health import get_health_status, get_readiness_status
from backend.app.domains.system.schemas import HealthResponse, ReadinessResponse

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
