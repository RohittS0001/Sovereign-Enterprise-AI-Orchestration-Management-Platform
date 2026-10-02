from fastapi import APIRouter

from backend.app.services.agent_service import process_agent_request


router = APIRouter(
    prefix="/api/v1/agent",
    tags=["Agent"],
)


@router.post("/query")
async def agent_query(query: str) -> dict[str, str]:
    return process_agent_request(query)