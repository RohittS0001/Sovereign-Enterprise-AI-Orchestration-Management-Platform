
from fastapi import APIRouter

from backend.app.services.health_service import get_health_status


router = APIRouter(
    prefix="/api/v1",
    tags=["Health"],
)


@router.get("/health")
async def health_check() -> dict[str, str]:
    return get_health_status()

