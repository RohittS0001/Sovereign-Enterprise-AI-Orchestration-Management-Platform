from fastapi import APIRouter

from backend.app.schemas.agent import AgentQueryRequest, AgentQueryResponse
from backend.app.services.agent_service import process_agent_request

router = APIRouter(
    prefix="/api/v1/agent",
    tags=["Agent"],
)


@router.post("/query", response_model=AgentQueryResponse)
async def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    return process_agent_request(request)
