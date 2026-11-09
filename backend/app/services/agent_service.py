from backend.app.core.exceptions.exceptions import AppException
from backend.app.schemas.agent import AgentQueryRequest, AgentQueryResponse


def process_agent_request(request: AgentQueryRequest) -> AgentQueryResponse:
    query = request.normalized_query()
    if not query:
        raise AppException("Query must not be blank", 422, code="validation_error")

    # The LangGraph orchestration pipeline is wired in Phase 3.
    return AgentQueryResponse(
        status="received",
        query=query,
        message="Agent processing is not implemented yet.",
    )
