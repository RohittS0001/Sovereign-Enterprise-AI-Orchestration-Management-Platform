def test_agent_query_accepts_json_body(client) -> None:
    response = client.post("/api/v1/agent/query", json={"query": "  Why is Phoenix late?  "})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "received"
    assert body["query"] == "Why is Phoenix late?"


def test_agent_query_rejects_missing_query(client) -> None:
    response = client.post("/api/v1/agent/query", json={})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "query"]


def test_agent_query_rejects_blank_query(client) -> None:
    response = client.post("/api/v1/agent/query", json={"query": "   "})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_agent_query_rejects_oversized_query(client) -> None:
    response = client.post("/api/v1/agent/query", json={"query": "x" * 4001})

    assert response.status_code == 422
