from fastapi import APIRouter, Request, Response

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


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"description": "The database is unreachable: not ready for traffic"}},
)
async def readiness_check(
    request: Request, response: Response, settings: SettingsDep
) -> ReadinessResponse:
    """200 while the required dependency (the database) answers, 503 otherwise, so a load
    balancer or Kubernetes stops routing to this instance. Optional integrations that are
    not configured show as "degraded" but do not make the instance unready."""
    status = await get_readiness_status(settings, request.app.state.database)
    database = next(d for d in status.dependencies if d.name == "database")
    if not database.healthy:
        response.status_code = 503
    return status
