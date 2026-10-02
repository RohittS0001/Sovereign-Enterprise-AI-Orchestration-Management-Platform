def process_agent_request(query: str) -> dict[str, str]:
    return {
        "status": "received",
        "query": query,
        "message": "Agent processing is not implemented yet.",
    }