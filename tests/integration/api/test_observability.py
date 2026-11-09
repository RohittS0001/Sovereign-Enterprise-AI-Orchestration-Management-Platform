from pydantic import SecretStr

from backend.app.core.observability import configure_tracing, span
from backend.app.main import create_app
from tests.conftest import serve
from tests.support.agent import DONE, build_registry, models, plan


def sample(text: str, name: str, **labels: str) -> float:
    """Read one sample value from Prometheus text exposition."""
    # Prometheus prints labels sorted by name.
    selector = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    prefix = f"{name}{{{selector}}}" if selector else name
    for line in text.splitlines():
        if line.startswith(prefix + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def test_metrics_endpoint_counts_requests_and_agent_activity(
    test_settings, database, make_user, auth_headers
) -> None:
    agent_models, _, _ = models(
        [plan(("jira.get_issue", {"issue_key": "PHX-7"})), DONE], ["ok [S1]"]
    )
    app = create_app(
        test_settings,
        database,
        agent_models=agent_models,
        tool_registry=build_registry(test_settings),
    )
    headers = auth_headers(await make_user())

    async with serve(app) as client:
        before = (await client.get("/metrics")).text
        await client.get("/api/v1/health")
        await client.post("/api/v1/agent/query", json={"query": "PHX-7?"}, headers=headers)
        after = (await client.get("/metrics")).text

    route = {"method": "GET", "route": "/api/v1/health", "status": "200"}
    assert (
        sample(after, "sovereign_http_requests_total", **route)
        == sample(before, "sovereign_http_requests_total", **route) + 1
    )
    tool = {"tool": "jira.get_issue", "status": "success"}
    assert sample(after, "sovereign_tool_calls_total", **tool) > sample(
        before, "sovereign_tool_calls_total", **tool
    )
    assert sample(after, "sovereign_agent_runs_total", status="completed") > sample(
        before, "sovereign_agent_runs_total", status="completed"
    )


async def test_metrics_token_and_disable(test_settings) -> None:
    protected = test_settings.model_copy(update={"metrics_token": SecretStr("scrape-secret")})
    disabled = test_settings.model_copy(update={"metrics_enabled": False})

    async with serve(create_app(protected)) as client:
        anonymous = await client.get("/metrics")
        wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
        right = await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    async with serve(create_app(disabled)) as client:
        off = await client.get("/metrics")

    assert anonymous.status_code == 401 and wrong.status_code == 401
    assert right.status_code == 200 and "sovereign_http_requests_total" in right.text
    assert off.status_code == 404


async def test_unmatched_routes_share_one_label(client) -> None:
    for path in ("/random-1", "/random-2", "/random-3"):
        await client.get(path)

    text = (await client.get("/metrics")).text

    assert 'route="unmatched"' in text
    assert "/random-1" not in text


def test_tracing_is_off_without_endpoint(test_settings) -> None:
    assert configure_tracing(test_settings) is False
    with span("noop", key="value") as current:
        current.set_attribute("x", 1)  # no-op span accepts attributes


async def test_spans_cover_agent_tool_and_http(
    test_settings, database, make_user, auth_headers
) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    agent_models, _, _ = models(
        [plan(("jira.get_issue", {"issue_key": "PHX-7"})), DONE], ["ok [S1]"]
    )
    app = create_app(
        test_settings,
        database,
        agent_models=agent_models,
        tool_registry=build_registry(test_settings),
    )
    headers = auth_headers(await make_user())
    async with serve(app) as client:
        await client.post("/api/v1/agent/query", json={"query": "PHX-7?"}, headers=headers)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert {"POST /api/v1/agent/query", "agent.run", "tool.invoke"} <= set(spans)
    assert spans["tool.invoke"].attributes["tool"] == "jira.get_issue"
    assert spans["tool.invoke"].attributes["tool.status"] == "success"
    assert spans["agent.run"].attributes["agent.status"] == "completed"
    # The tool span is nested inside the agent run, which is inside the HTTP request.
    assert (
        spans["tool.invoke"].context.trace_id == spans["POST /api/v1/agent/query"].context.trace_id
    )
